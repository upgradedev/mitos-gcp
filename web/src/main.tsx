import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./index.css";

const host = document.getElementById("root");
if (!host) throw new Error("no #root in the document");

createRoot(host).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
