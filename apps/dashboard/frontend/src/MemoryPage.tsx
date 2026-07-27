import { FormEvent, useEffect, useState } from "react";

import { api } from "./api";

type Fact = { id: string; category: string; fact: string };
type Activity = "idle" | "loading" | "saving" | "deleting";

const categories = ["name", "preference", "place_label", "relationship"];
const categoryLabels: Record<string, string> = {
  name: "Név",
  preference: "Beállítás",
  place_label: "Helymegjelölés",
  relationship: "Kapcsolat",
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "ismeretlen hiba";
}

export function MemoryPage() {
  const [facts, setFacts] = useState<Fact[]>([]);
  const [status, setStatus] = useState("A session-memória betöltése folyamatban van…");
  const [hasError, setHasError] = useState(false);
  const [editing, setEditing] = useState<Fact | null>(null);
  const [activity, setActivity] = useState<Activity>("idle");
  const busy = activity !== "idle";

  async function loadFacts(successMessage: string, failurePrefix = "A memória nem olvasható") {
    setActivity("loading");
    setHasError(false);
    setStatus("A session-memória frissítése folyamatban van…");
    try {
      const data = await api<{ facts: Fact[] }>("/api/v1/memory");
      setFacts(data.facts);
      setStatus(data.facts.length === 0 ? "Ebben a böngészősessionben még nincs mentett, megjeleníthető emlék." : successMessage);
    } catch (error) {
      setHasError(true);
      setStatus(`${failurePrefix}: ${errorMessage(error)}`);
    } finally {
      setActivity("idle");
    }
  }

  async function refresh() {
    if (busy) return;
    await loadFacts("A session-memória frissítve.");
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!editing || busy) return;

    setActivity("saving");
    setHasError(false);
    setStatus("Az emlék mentése folyamatban van…");
    try {
      await api(`/api/v1/memory/${encodeURIComponent(editing.id)}`, {
        method: "PUT",
        body: JSON.stringify({ category: editing.category, fact: editing.fact.trim() }),
      });
      setEditing(null);
      await loadFacts("Az emlék mentve, a session-memória frissítve.", "Az emlék mentve, de a lista nem frissíthető");
    } catch (error) {
      setHasError(true);
      setStatus(`Az emlék nem menthető: ${errorMessage(error)}`);
      setActivity("idle");
    }
  }

  async function erase(id: string) {
    if (busy) return;

    setActivity("deleting");
    setHasError(false);
    setStatus("Az emlék törlése folyamatban van…");
    try {
      await api(`/api/v1/memory/${encodeURIComponent(id)}`, { method: "DELETE" });
      if (editing?.id === id) setEditing(null);
      await loadFacts("Az emlék törölve, a session-memória frissítve.", "Az emlék törölve, de a lista nem frissíthető");
    } catch (error) {
      setHasError(true);
      setStatus(`Az emlék nem törölhető: ${errorMessage(error)}`);
      setActivity("idle");
    }
  }

  return (
    <section className="content-card" aria-labelledby="memory-title">
      <div className="section-heading">
        <div>
          <p className="eyebrow">SESSION-MEMÓRIA</p>
          <h2 id="memory-title">Memória</h2>
        </div>
        <button type="button" onClick={() => void refresh()} disabled={busy}>
          {activity === "loading" ? "Frissítés folyamatban…" : "Frissítés"}
        </button>
      </div>
      <p className="muted">Tárolási határ: csak ez a böngészősession. A lista nem közös és nem ad felhatalmazást küldetés indítására.</p>
      <p className="muted">Adatkezelés: ne ments érzékeny, azonosító vagy hitelesítési adatot.</p>
      <p className="muted" role={hasError ? "alert" : "status"} aria-live="polite" aria-atomic="true">{status}</p>

      {hasError ? (
        <p className="muted">Próbáld meg újra a frissítést.</p>
      ) : facts.length === 0 && activity !== "loading" ? (
        <p className="muted">Nincs megjeleníthető emlék ebben a sessionben.</p>
      ) : (
        <ul className="data-list" aria-label="Session-memória bejegyzései">
          {facts.map((fact) => (
            <li key={fact.id}>
              {editing?.id === fact.id ? (
                <form className="memory-edit" onSubmit={save}>
                  <p className="muted">Szerkesztés előtt ellenőrizd, hogy a szöveg nem érzékeny adat.</p>
                  <label>
                    Kategória
                    <select value={editing.category} onChange={(event) => setEditing({ ...editing, category: event.target.value })}>
                      {categories.map((category) => <option key={category} value={category}>{categoryLabels[category]}</option>)}
                    </select>
                  </label>
                  <label>
                    Emlék szövege
                    <input aria-label="Emlék szövege" maxLength={240} value={editing.fact} onChange={(event) => setEditing({ ...editing, fact: event.target.value })} />
                  </label>
                  <button className="primary" disabled={busy || !editing.fact.trim()}>Mentés</button>
                  <button type="button" onClick={() => setEditing(null)} disabled={busy}>Mégse</button>
                </form>
              ) : (
                <>
                  <strong>{categoryLabels[fact.category] ?? fact.category}</strong>
                  <span>{fact.fact}</span>
                  <small>Session-bejegyzés · csak az operátor által kezelt kontextus</small>
                  <span className="memory-actions">
                    <button type="button" onClick={() => setEditing(fact)} disabled={busy}>Szerkesztés</button>
                    <button type="button" onClick={() => void erase(fact.id)} disabled={busy}>Törlés</button>
                  </span>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
