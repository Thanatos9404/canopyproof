import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./kit/styles/fonts";
import "./kit/styles/tokens.css";
import "leaflet/dist/leaflet.css";
import "./styles/app.css";
import { Landing } from "./landing/Landing";
import { ProductApp } from "./app/ProductApp";

/** Hash routing: "#app", "#app/<screen>[/<id>]" open the monitor; anything else is the landing page. */
function Root() {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const on = () => setHash(location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return hash.startsWith("#app") ? <ProductApp hash={hash} /> : <Landing />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
