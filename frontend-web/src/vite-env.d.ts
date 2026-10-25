/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL?: string;
  readonly VITE_WS_URL?: string;
  /** The bank's fraud line for "Call my bank" (T5.4); the 1930 helpline when unset. */
  readonly VITE_BANK_PHONE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
