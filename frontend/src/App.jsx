import { Navigate, Route, Routes } from "react-router-dom";
import ReviewDashboard from "./pages/ReviewDashboard";

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<ReviewDashboard />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
