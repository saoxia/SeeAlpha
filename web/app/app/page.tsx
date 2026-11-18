import { AuthGate } from "../components/auth/auth-gate";

export default function AppPage() {
  return <AuthGate destination="app" />;
}
