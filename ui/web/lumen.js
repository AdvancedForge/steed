"use strict";
// Lumen: the card under the pointer catches a soft light where the pointer
// is (lumen.css draws it from --mx/--my). One listener, one update a frame.
(() => {
  if (document.documentElement.dataset.effects === "minimal") return;
  const CARDS = ".form-group, .settings-card, .profile-row, .card";
  let lit = null, frame = 0, last = null;
  const update = () => {
    frame = 0;
    const card = last && last.target instanceof Element ? last.target.closest(CARDS) : null;
    if (card !== lit) { lit?.classList.remove("lit"); lit = card; lit?.classList.add("lit"); }
    if (!card) return;
    const box = card.getBoundingClientRect();
    card.style.setProperty("--mx", `${last.clientX - box.left}px`);
    card.style.setProperty("--my", `${last.clientY - box.top}px`);
  };
  addEventListener("pointermove", event => { last = event; if (!frame) frame = requestAnimationFrame(update); }, {passive: true});
  addEventListener("pointerleave", () => { lit?.classList.remove("lit"); lit = null; });
})();
