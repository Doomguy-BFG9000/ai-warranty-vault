import type { Session, User } from '@supabase/supabase-js';
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

import { supabase } from '@/lib/supabase';

interface AuthState {
  session: Session | null;
  user: User | null;
  /** True until the persisted session has been read from secure storage. */
  initializing: boolean;
}

interface AuthContextValue extends AuthState {
  signIn: (email: string, password: string) => Promise<void>;
  signUp: (email: string, password: string) => Promise<{ needsEmailConfirmation: boolean }>;
  signOut: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    session: null,
    user: null,
    initializing: true,
  });

  useEffect(() => {
    let active = true;

    // Restore any persisted session before deciding which routes to show,
    // otherwise a signed-in user briefly sees the sign-in screen on cold start.
    supabase.auth.getSession().then(({ data }) => {
      if (!active) return;
      setState({
        session: data.session,
        user: data.session?.user ?? null,
        initializing: false,
      });
    });

    // Covers sign-in, sign-out, and background token refreshes.
    const { data: subscription } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!active) return;
      setState({ session, user: session?.user ?? null, initializing: false });
    });

    return () => {
      active = false;
      subscription.subscription.unsubscribe();
    };
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,

      async signIn(email, password) {
        const { error } = await supabase.auth.signInWithPassword({
          email: email.trim(),
          password,
        });
        if (error) throw error;
      },

      async signUp(email, password) {
        const { data, error } = await supabase.auth.signUp({
          email: email.trim(),
          password,
        });
        if (error) throw error;
        // With email confirmation enabled the user exists but has no session
        // yet; the caller tells them to check their inbox instead of routing on.
        return { needsEmailConfirmation: !data.session };
      },

      async signOut() {
        const { error } = await supabase.auth.signOut();
        if (error) throw error;
      },
    }),
    [state],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within an AuthProvider');
  return context;
}

/**
 * The signed-in user's id, for queries that require it.
 * Throws when called outside an authenticated route, which would be a routing bug.
 */
export function useUserId(): string {
  const { user } = useAuth();
  if (!user) throw new Error('useUserId called while signed out');
  return user.id;
}
