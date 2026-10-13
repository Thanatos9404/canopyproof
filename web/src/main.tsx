import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./kit/styles/fonts";
import "./kit/styles/tokens.css";
import "leaflet/dist/leaflet.css";
import "./styles/app.css";
import { Landing } from "./landing/Landing";
import { ProductApp } from "./app/ProductApp";

const hashRoot = (h: string) => (h.startsWith("#app") ? h.split("/")[1] ?? "overview" : "landing");

/** Hash routing: "#app", "#app/<screen>[/<id>]" open the monitor; anything else is the landing page. */
function Root() {
  const [hash, setHash] = useState(location.hash);
  useEffect(() => {
    const on = () => {
      const was = hashRoot(hash);
      setHash(location.hash);
      if (hashRoot(location.hash) !== was || location.hash.startsWith("#app")) window.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, [hash]);
  return hash.startsWith("#app") ? <ProductApp hash={hash} /> : <Landing />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
