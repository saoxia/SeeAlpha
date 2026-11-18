import { AuthGate } from "../components/auth/auth-gate";

export default function AdminPage() {
  return <AuthGate destination="admin" />;
}
