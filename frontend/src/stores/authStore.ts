import { create } from "zustand";

export interface AuthUser {
  id: number;
  username: string;
  display_name: string;
  role: string;
  auth_mode: "local" | "oidc";
}

interface AuthState {
  /** undefined = still checking; null = must sign in; object = signed in. */
  user: AuthUser | null | undefined;
  check: () => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  user: undefined,

  check: async () => {
    try {
      // Raw fetch: the shared json() helper redirects on 401, which is
      // exactly what must NOT happen here — the login screen handles it.
      const response = await fetch("/auth/me");
      set({ user: response.ok ? ((await response.json()) as AuthUser) : null });
    } catch {
      set({ user: null });
    }
  },
}));
