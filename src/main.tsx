import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import App from "./App";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

if (isTauri()) {
  requestAnimationFrame(() => {
    requestAnimationFrame(() => {
      const window = getCurrentWindow();
      void window.show().then(() => window.setFocus()).catch(console.error);
    });
  });
}
