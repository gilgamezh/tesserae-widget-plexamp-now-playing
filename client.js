// plexamp_now_playing — cover + album + artist for one Plexamp player,
// or a shelf pick when idle. Landscape: cover left, text right.
// Portrait: cover top, text below. Two cover-first fragments: "poster"
// (cover full-bleed, cropped, solid caption band at the bottom) and
// "gallery" (whole square cover, one small line above and one below),
// "overlay" (cover fills the panel, ~12% cropped top and bottom, one small
// caption on it, bottom right). The manifest's render.full_bleed drops the
// page gap so these reach the panel edge.
// Container-query sizing, Spectra tokens,
// no client fetch (art arrives as a server-inlined data URI).
//
// The label deliberately does not distinguish playing from paused: a
// pause/resume would otherwise change the frame and force a repaint.

export default function (shadow, ctx) {
  const d = ctx.data || {};
  // Grid cells always get fragment "full" (only the Panels canvas picks
  // fragments), so the `layout` cell option chooses there instead.
  const cellFragment = (ctx.cell && ctx.cell.fragment) || "full";
  const layoutOpt = (ctx.cell && ctx.cell.options && ctx.cell.options.layout) || "full";
  const fragment = cellFragment !== "full" ? cellFragment : layoutOpt;

  const esc = (s) =>
    String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

  const label = d.mode === "shelf" ? "From the shelf" : "Now playing";
  const icon = d.mode === "shelf" ? "ph-books" : "ph-vinyl-record";

  const cover = d.art
    ? `<img src="${d.art}" alt="" />`
    : `<div class="ph-holder"><i class="ph ph-bold ph-vinyl-record"></i></div>`;

  const styles = `
    <link rel="stylesheet" href="/static/style/spectra-widgets.css" />
    <style>
      .w { box-sizing: border-box; width: 100%; height: 100%; overflow: hidden;
           container-type: size; background: var(--surface); color: var(--text-primary); }
      .np { width: 100%; height: 100%; display: flex; gap: 5cqmin; padding: 5cqmin;
            box-sizing: border-box; align-items: center; }
      .art { flex: 0 0 auto; aspect-ratio: 1; height: 100%; max-width: 60%;
             overflow: hidden; background: var(--surface-sunken); position: relative; }
      .art img { width: 100%; height: 100%; object-fit: cover; display: block; }
      .ph-holder { position: absolute; inset: 0; display: flex; align-items: center;
                   justify-content: center; }
      .ph-holder .ph { font-size: 30cqmin; color: var(--text-secondary); }
      .txt { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column;
             gap: 2.5cqmin; justify-content: center; }
      .over { display: flex; align-items: center; gap: 1.5cqmin;
              font-size: 5cqmin; font-weight: var(--fw-black, 800);
              text-transform: uppercase; letter-spacing: 0.08em; color: var(--accent-4); }
      .album { font-size: 9cqmin; font-weight: var(--fw-black, 800); line-height: 1.05;
               display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical;
               overflow: hidden; overflow-wrap: anywhere; }
      .artist { font-size: 6.5cqmin; font-weight: 700; line-height: 1.15;
                display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical;
                overflow: hidden; }
      .track, .year { font-size: 5cqmin; color: var(--text-secondary);
                      white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .demo { font-size: 4cqmin; color: var(--text-secondary); }
      .err { padding: 6cqmin; font-size: 5cqmin; }

      @container (aspect-ratio < 1) {
        .np { flex-direction: column; align-items: stretch; }
        .art { width: 100%; height: auto; max-width: none; }
        .album { font-size: 8cqmin; -webkit-line-clamp: 2; }
      }

      /* Solid bands, never gradients: a gradient over a photo dithers to noise. */
      .poster { position: relative; width: 100%; height: 100%; }
      .poster .art { position: absolute; inset: 0; width: 100%; height: 100%;
                     max-width: none; aspect-ratio: auto; }
      .band { position: absolute; left: 0; right: 0; bottom: 0; box-sizing: border-box;
              padding: 1.6cqh 3cqw; background: var(--surface); display: flex;
              flex-direction: column; gap: 0.4cqh; }
      .band .album { font-size: 5.6cqh; line-height: 1.1; -webkit-line-clamp: 1;
                     white-space: nowrap; text-overflow: ellipsis; display: block; }
      .meta { display: flex; align-items: center; gap: 1.5cqw; font-size: 4.2cqh;
              font-weight: 700; color: var(--text-secondary); min-width: 0; }
      .meta .who { flex: 1 1 auto; min-width: 0; white-space: nowrap; overflow: hidden;
                   text-overflow: ellipsis; }
      .meta .ph { color: var(--accent-4); }

      .gallery { width: 100%; height: 100%; box-sizing: border-box; padding: 1.5cqh 3cqw;
                 display: flex; flex-direction: column; align-items: center; gap: 1.5cqh; }
      .gallery .line { width: 100%; display: flex; align-items: center; gap: 1.5cqw;
                       font-size: 4.2cqh; line-height: 1.15; min-width: 0; }
      .gallery .over { font-size: 4.2cqh; flex: 1 1 auto; }
      .gallery .yr { color: var(--text-secondary); font-weight: 700; }
      .gallery .art { flex: 1 1 auto; height: auto; min-height: 0; max-width: 100%; }
      .gallery .cap { justify-content: center; font-weight: 700; }
      .gallery .cap span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .gallery .cap b { font-weight: var(--fw-black, 800); }

      /* Hard, unblurred shadow: a soft one dithers to grey fuzz on e-ink. */
      .overlay { position: relative; width: 100%; height: 100%; }
      .overlay .art { position: absolute; inset: 0; width: 100%; height: 100%;
                      max-width: none; aspect-ratio: auto; }
      .overlay .cap { position: absolute; right: 2.5cqh; bottom: 2cqh; max-width: 90%;
                      font-size: 4.5cqh; font-weight: var(--fw-black, 800); line-height: 1.1;
                      color: #fff; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
                      text-shadow: 1px 1px 0 #000, -1px -1px 0 #000, 1px -1px 0 #000,
                                   -1px 1px 0 #000, 2px 2px 0 #000; }

      .cover-only { width: 100%; height: 100%; }
      .cover-only .art { width: 100%; height: 100%; max-width: none; aspect-ratio: auto; }
    </style>`;

  let body;
  if (d.error) {
    body = `<div class="err"><i class="ph ph-bold ph-plugs"></i> ${esc(d.error)}</div>`;
  } else if (d.mode === "empty") {
    body = `<div class="err">${esc(d.note || "Nothing to show.")}</div>`;
  } else if (fragment === "poster") {
    body = `
      <div class="poster">
        <div class="art">${cover}</div>
        <div class="band">
          <div class="album">${esc(d.album)}</div>
          <div class="meta">
            <span class="who">${esc(d.artist)}${d.year ? ` · ${esc(d.year)}` : ""}${d.track ? ` · ${esc(d.track)}` : ""}</span>
            <i class="ph ph-bold ${icon}"></i>
          </div>
        </div>
      </div>`;
  } else if (fragment === "gallery") {
    body = `
      <div class="gallery">
        <div class="line">
          <div class="over"><i class="ph ph-bold ${icon}"></i>${esc(label)}</div>
          ${d.year ? `<span class="yr">${esc(d.year)}</span>` : ""}
        </div>
        <div class="art">${cover}</div>
        <div class="line cap">
          <span><b>${esc(d.album)}</b> — ${esc(d.artist)}${d.track ? ` · ${esc(d.track)}` : ""}</span>
        </div>
      </div>`;
  } else if (fragment === "overlay") {
    body = `
      <div class="overlay">
        <div class="art">${cover}</div>
        <div class="cap">${esc(d.album)} — ${esc(d.artist)}${d.track ? ` · ${esc(d.track)}` : ""}</div>
      </div>`;
  } else if (fragment === "cover") {
    body = `<div class="cover-only"><div class="art">${cover}</div></div>`;
  } else {
    body = `
      <div class="np">
        <div class="art">${cover}</div>
        <div class="txt">
          <div class="over"><i class="ph ph-bold ${icon}"></i>${esc(label)}</div>
          <div class="album">${esc(d.album)}</div>
          <div class="artist">${esc(d.artist)}</div>
          ${d.track ? `<div class="track">${esc(d.track)}</div>` : ""}
          ${d.year ? `<div class="year">${esc(d.year)}</div>` : ""}
          ${d.demo ? `<div class="demo">demo</div>` : ""}
        </div>
      </div>`;
  }

  shadow.innerHTML = `${styles}<div class="w">${body}</div>`;
}
