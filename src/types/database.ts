export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.5"
  }
  graphql_public: {
    Tables: {
      [_ in never]: never
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      graphql: {
        Args: {
          extensions?: Json
          operationName?: string
          query?: string
          variables?: Json
        }
        Returns: Json
      }
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
  public: {
    Tables: {
      ai_messages: {
        Row: {
          citations: Json
          content: string
          created_at: string
          id: string
          meta: Json
          outcome: string | null
          role: Database["public"]["Enums"]["ai_message_role"]
          thread_id: string
          user_id: string
        }
        Insert: {
          citations?: Json
          content: string
          created_at?: string
          id?: string
          meta?: Json
          outcome?: string | null
          role: Database["public"]["Enums"]["ai_message_role"]
          thread_id: string
          user_id: string
        }
        Update: {
          citations?: Json
          content?: string
          created_at?: string
          id?: string
          meta?: Json
          outcome?: string | null
          role?: Database["public"]["Enums"]["ai_message_role"]
          thread_id?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "ai_messages_thread_id_fkey"
            columns: ["thread_id"]
            isOneToOne: false
            referencedRelation: "ai_threads"
            referencedColumns: ["id"]
          },
        ]
      }
      ai_operation_log: {
        Row: {
          created_at: string
          detail: Json
          duration_ms: number | null
          error_code: string | null
          id: number
          input_tokens: number | null
          model: string | null
          operation_id: string
          output_tokens: number | null
          prompt_version: string | null
          stage: string
          status: string
          user_id: string | null
        }
        Insert: {
          created_at?: string
          detail?: Json
          duration_ms?: number | null
          error_code?: string | null
          id?: number
          input_tokens?: number | null
          model?: string | null
          operation_id: string
          output_tokens?: number | null
          prompt_version?: string | null
          stage: string
          status: string
          user_id?: string | null
        }
        Update: {
          created_at?: string
          detail?: Json
          duration_ms?: number | null
          error_code?: string | null
          id?: number
          input_tokens?: number | null
          model?: string | null
          operation_id?: string
          output_tokens?: number | null
          prompt_version?: string | null
          stage?: string
          status?: string
          user_id?: string | null
        }
        Relationships: []
      }
      ai_threads: {
        Row: {
          created_at: string
          id: string
          product_id: string | null
          title: string | null
          updated_at: string
          user_id: string
        }
        Insert: {
          created_at?: string
          id?: string
          product_id?: string | null
          title?: string | null
          updated_at?: string
          user_id: string
        }
        Update: {
          created_at?: string
          id?: string
          product_id?: string | null
          title?: string | null
          updated_at?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "ai_threads_product_id_fkey"
            columns: ["product_id"]
            isOneToOne: false
            referencedRelation: "products"
            referencedColumns: ["id"]
          },
        ]
      }
      claim_drafts: {
        Row: {
          approved_at: string | null
          approved_body: string | null
          approved_subject: string | null
          body: string
          citations: Json
          content_sha256: string
          created_at: string
          facts: Json
          id: string
          product_id: string
          status: Database["public"]["Enums"]["claim_draft_status"]
          subject: string
          thread_id: string
          updated_at: string
          user_id: string
          version: number
        }
        Insert: {
          approved_at?: string | null
          approved_body?: string | null
          approved_subject?: string | null
          body: string
          citations?: Json
          content_sha256: string
          created_at?: string
          facts?: Json
          id?: string
          product_id: string
          status?: Database["public"]["Enums"]["claim_draft_status"]
          subject: string
          thread_id: string
          updated_at?: string
          user_id: string
          version?: number
        }
        Update: {
          approved_at?: string | null
          approved_body?: string | null
          approved_subject?: string | null
          body?: string
          citations?: Json
          content_sha256?: string
          created_at?: string
          facts?: Json
          id?: string
          product_id?: string
          status?: Database["public"]["Enums"]["claim_draft_status"]
          subject?: string
          thread_id?: string
          updated_at?: string
          user_id?: string
          version?: number
        }
        Relationships: [
          {
            foreignKeyName: "claim_drafts_product_id_fkey"
            columns: ["product_id"]
            isOneToOne: false
            referencedRelation: "products"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "claim_drafts_thread_id_fkey"
            columns: ["thread_id"]
            isOneToOne: false
            referencedRelation: "ai_threads"
            referencedColumns: ["id"]
          },
        ]
      }
      document_chunks: {
        Row: {
          chunk_index: number
          content: string
          content_sha256: string
          content_tsv: unknown
          created_at: string
          document_id: string
          embedding: string | null
          embedding_dim: number
          embedding_model: string
          id: string
          index_version: string
          locator: string
          page_number: number | null
          product_id: string
          token_count: number
          user_id: string
        }
        Insert: {
          chunk_index: number
          content: string
          content_sha256: string
          content_tsv?: unknown
          created_at?: string
          document_id: string
          embedding?: string | null
          embedding_dim: number
          embedding_model: string
          id?: string
          index_version: string
          locator: string
          page_number?: number | null
          product_id: string
          token_count: number
          user_id: string
        }
        Update: {
          chunk_index?: number
          content?: string
          content_sha256?: string
          content_tsv?: unknown
          created_at?: string
          document_id?: string
          embedding?: string | null
          embedding_dim?: number
          embedding_model?: string
          id?: string
          index_version?: string
          locator?: string
          page_number?: number | null
          product_id?: string
          token_count?: number
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "document_chunks_document_id_fkey"
            columns: ["document_id"]
            isOneToOne: false
            referencedRelation: "product_documents"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "document_chunks_product_id_fkey"
            columns: ["product_id"]
            isOneToOne: false
            referencedRelation: "products"
            referencedColumns: ["id"]
          },
        ]
      }
      document_index_jobs: {
        Row: {
          attempts: number
          created_at: string
          document_id: string
          id: string
          index_version: string | null
          last_error: string | null
          locked_at: string | null
          locked_by: string | null
          max_attempts: number
          run_after: string
          state: Database["public"]["Enums"]["index_job_state"]
          updated_at: string
          user_id: string
        }
        Insert: {
          attempts?: number
          created_at?: string
          document_id: string
          id?: string
          index_version?: string | null
          last_error?: string | null
          locked_at?: string | null
          locked_by?: string | null
          max_attempts?: number
          run_after?: string
          state?: Database["public"]["Enums"]["index_job_state"]
          updated_at?: string
          user_id: string
        }
        Update: {
          attempts?: number
          created_at?: string
          document_id?: string
          id?: string
          index_version?: string | null
          last_error?: string | null
          locked_at?: string | null
          locked_by?: string | null
          max_attempts?: number
          run_after?: string
          state?: Database["public"]["Enums"]["index_job_state"]
          updated_at?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "document_index_jobs_document_id_fkey"
            columns: ["document_id"]
            isOneToOne: false
            referencedRelation: "product_documents"
            referencedColumns: ["id"]
          },
        ]
      }
      product_documents: {
        Row: {
          byte_size: number
          chunk_count: number
          content_sha256: string | null
          created_at: string
          embedding_dim: number | null
          embedding_model: string | null
          error_code: string | null
          error_message: string | null
          extraction_method:
            | Database["public"]["Enums"]["document_extraction_method"]
            | null
          id: string
          index_version: string | null
          indexed_at: string | null
          kind: Database["public"]["Enums"]["document_kind"]
          mime_type: string
          page_count: number | null
          product_id: string
          status: Database["public"]["Enums"]["document_index_status"]
          storage_path: string
          title: string
          updated_at: string
          user_id: string
        }
        Insert: {
          byte_size: number
          chunk_count?: number
          content_sha256?: string | null
          created_at?: string
          embedding_dim?: number | null
          embedding_model?: string | null
          error_code?: string | null
          error_message?: string | null
          extraction_method?:
            | Database["public"]["Enums"]["document_extraction_method"]
            | null
          id?: string
          index_version?: string | null
          indexed_at?: string | null
          kind?: Database["public"]["Enums"]["document_kind"]
          mime_type: string
          page_count?: number | null
          product_id: string
          status?: Database["public"]["Enums"]["document_index_status"]
          storage_path: string
          title: string
          updated_at?: string
          user_id: string
        }
        Update: {
          byte_size?: number
          chunk_count?: number
          content_sha256?: string | null
          created_at?: string
          embedding_dim?: number | null
          embedding_model?: string | null
          error_code?: string | null
          error_message?: string | null
          extraction_method?:
            | Database["public"]["Enums"]["document_extraction_method"]
            | null
          id?: string
          index_version?: string | null
          indexed_at?: string | null
          kind?: Database["public"]["Enums"]["document_kind"]
          mime_type?: string
          page_count?: number | null
          product_id?: string
          status?: Database["public"]["Enums"]["document_index_status"]
          storage_path?: string
          title?: string
          updated_at?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "product_documents_product_id_fkey"
            columns: ["product_id"]
            isOneToOne: false
            referencedRelation: "products"
            referencedColumns: ["id"]
          },
        ]
      }
      products: {
        Row: {
          category: string | null
          created_at: string
          currency: string
          extraction_status: Database["public"]["Enums"]["extraction_status"]
          id: string
          merchant: string | null
          name: string
          notes: string | null
          price_cents: number | null
          purchase_date: string | null
          receipt_image_path: string | null
          updated_at: string
          user_id: string
          warranty_expires_at: string | null
          warranty_months: number | null
        }
        Insert: {
          category?: string | null
          created_at?: string
          currency?: string
          extraction_status?: Database["public"]["Enums"]["extraction_status"]
          id?: string
          merchant?: string | null
          name: string
          notes?: string | null
          price_cents?: number | null
          purchase_date?: string | null
          receipt_image_path?: string | null
          updated_at?: string
          user_id: string
          warranty_expires_at?: string | null
          warranty_months?: number | null
        }
        Update: {
          category?: string | null
          created_at?: string
          currency?: string
          extraction_status?: Database["public"]["Enums"]["extraction_status"]
          id?: string
          merchant?: string | null
          name?: string
          notes?: string | null
          price_cents?: number | null
          purchase_date?: string | null
          receipt_image_path?: string | null
          updated_at?: string
          user_id?: string
          warranty_expires_at?: string | null
          warranty_months?: number | null
        }
        Relationships: []
      }
      profiles: {
        Row: {
          created_at: string
          email: string | null
          expo_push_token: string | null
          id: string
          is_premium: boolean
          updated_at: string
        }
        Insert: {
          created_at?: string
          email?: string | null
          expo_push_token?: string | null
          id: string
          is_premium?: boolean
          updated_at?: string
        }
        Update: {
          created_at?: string
          email?: string | null
          expo_push_token?: string | null
          id?: string
          is_premium?: boolean
          updated_at?: string
        }
        Relationships: []
      }
      reminders: {
        Row: {
          channel: string
          created_at: string
          id: string
          product_id: string
          remind_at: string
          sent_at: string | null
          user_id: string
        }
        Insert: {
          channel?: string
          created_at?: string
          id?: string
          product_id: string
          remind_at: string
          sent_at?: string | null
          user_id: string
        }
        Update: {
          channel?: string
          created_at?: string
          id?: string
          product_id?: string
          remind_at?: string
          sent_at?: string | null
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "reminders_product_id_fkey"
            columns: ["product_id"]
            isOneToOne: false
            referencedRelation: "products"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "reminders_user_id_profiles_fkey"
            columns: ["user_id"]
            isOneToOne: false
            referencedRelation: "profiles"
            referencedColumns: ["id"]
          },
        ]
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      dispatch_due_reminders: { Args: never; Returns: undefined }
      free_tier_product_limit: { Args: never; Returns: number }
      product_warranty_facts: {
        Args: { p_product_id: string }
        Returns: {
          days_remaining: number
          product_id: string
          purchase_date: string
          status: string
          warranty_expires_at: string
          warranty_months: number
        }[]
      }
      search_document_chunks: {
        Args: {
          p_candidate_count?: number
          p_document_id?: string
          p_index_version?: string
          p_match_count?: number
          p_mode?: string
          p_product_id?: string
          p_query_embedding: string
          p_query_text?: string
        }
        Returns: {
          chunk_id: string
          content: string
          document_id: string
          lexical_position: number
          lexical_rank: number
          locator: string
          page_number: number
          product_id: string
          score: number
          token_count: number
          vector_position: number
          vector_similarity: number
        }[]
      }
    }
    Enums: {
      ai_message_role: "user" | "assistant"
      claim_draft_status: "pending" | "approved" | "rejected" | "superseded"
      document_extraction_method: "pdf_text" | "plain_text" | "vision_ocr"
      document_index_status:
        | "pending"
        | "extracting"
        | "chunking"
        | "embedding"
        | "ready"
        | "failed"
      document_kind: "manual" | "warranty_card" | "service_terms" | "other"
      extraction_status: "pending" | "done" | "failed"
      index_job_state: "queued" | "running" | "succeeded" | "failed"
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends (DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never) = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends (PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never) = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  graphql_public: {
    Enums: {},
  },
  public: {
    Enums: {
      ai_message_role: ["user", "assistant"],
      claim_draft_status: ["pending", "approved", "rejected", "superseded"],
      document_extraction_method: ["pdf_text", "plain_text", "vision_ocr"],
      document_index_status: [
        "pending",
        "extracting",
        "chunking",
        "embedding",
        "ready",
        "failed",
      ],
      document_kind: ["manual", "warranty_card", "service_terms", "other"],
      extraction_status: ["pending", "done", "failed"],
      index_job_state: ["queued", "running", "succeeded", "failed"],
    },
  },
} as const
