import { useEffect, type ReactNode } from "react";
import cmuqLogo from "../assets/cmuq-logo.png";
import { useAuthStore } from "../stores/authStore";

/** Shown before anything else in OIDC deployments; the dashboard renders
 *  only after a successful sign-in. Local mode passes straight through. */
function LoginScreen() {
  return (
    <div className="login-screen">
      <div className="login-card">
        <img src={cmuqLogo} alt="Carnegie Mellon University Qatar" className="login-logo" />
        <h1>PatrolBot Dashboard</h1>
        <p className="subtext">
          Access is limited to authorized members of the PatrolBot project.
          Sign in with your Andrew ID to continue.
        </p>
        <a className="btn primary login-button" href="/auth/login">
          Sign in with your Andrew ID
        </a>
      </div>
    </div>
  );
}

export function AuthGate({ children }: { children: ReactNode }) {
  const user = useAuthStore((state) => state.user);
  const check = useAuthStore((state) => state.check);

  useEffect(() => {
    void check();
  }, [check]);

  if (user === undefined) {
    return <div className="login-screen" aria-busy="true" />;
  }
  if (user === null) {
    return <LoginScreen />;
  }
  return <>{children}</>;
}
