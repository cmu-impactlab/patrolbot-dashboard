import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { useUiStore } from "./stores/uiStore";
import "./themes/tokens.css";
import "./themes/app.css";
import "react-grid-layout/css/styles.css";
import "react-resizable/css/styles.css";

document.documentElement.dataset.theme = useUiStore.getState().theme;

const queryClient = new QueryClient();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
