import { create } from "zustand";

export interface AuthUser {
  id: number;
  username: string;
  display_name: string;
  role: string;
  auth_mode: "local" | "oidc";
}

/**
 * Mirrors server/app/authentication/local.py. Presentation only — the server
 * decides, and returns 403 regardless of what the browser rendered. This is
 * here so an observer sees a disabled control with a reason instead of a
 * button that always fails.
 */
export function canCommand(user: AuthUser | null | undefined): boolean {
  return user?.role === "operator" || user?.role === "administrator";
}

export function isAdministrator(user: AuthUser | null | undefined): boolean {
  return user?.role === "administrator";
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
