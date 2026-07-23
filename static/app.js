// Matches the in-game Player Profile's own equipment order
// (wiki.warframe.com/w/Player_Profile), not alphabetical.
const CATEGORY_ORDER = [
    "Warframes", "Primary", "Secondary", "Melee", "Robotic",
    "Companions", "Vehicles", "Arch-Gun", "Arch-Melee", "Amps",
];

function sortByCategoryOrder(categories) {
    return categories.slice().sort((a, b) => {
        const ia = CATEGORY_ORDER.indexOf(a);
        const ib = CATEGORY_ORDER.indexOf(b);
        if (ia === -1 && ib === -1) return a.localeCompare(b);
        if (ia === -1) return 1;
        if (ib === -1) return -1;
        return ia - ib;
    });
}

function escapeHtml(str) {
    if (str == null) return "";
    return String(str).replace(/[&<>"']/g, (c) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
}

function safeWikiUrl(url) {
    // wikiaUrl comes from a third-party dataset fetched over the network;
    // only allow it through as a link if it's genuinely an https:// URL.
    if (!url) return null;
    try {
        const parsed = new URL(url, window.location.origin);
        return parsed.protocol === "https:" ? parsed.href : null;
    } catch {
        return null;
    }
}

function itemIconHtml(imageName, sizeClass) {
    return imageName
        ? `<img class="${sizeClass}" src="/icons/${encodeURIComponent(imageName)}" alt="" />`
        : `<div class="${sizeClass}-placeholder"></div>`;
}

function attachIconErrorFallback(root, sizeClass) {
    root.querySelectorAll(`img.${sizeClass}`).forEach(img => {
        img.addEventListener("error", () => {
            const placeholder = document.createElement("div");
            placeholder.className = `${sizeClass}-placeholder`;
            img.replaceWith(placeholder);
        }, { once: true });
    });
}

function rarityClass(rarity) {
    switch ((rarity || "").toLowerCase()) {
        case "rare": return "rarity-rare";
        case "uncommon": return "rarity-uncommon";
        case "common": return "rarity-common";
        default: return "";
    }
}

function currentPlayerId() {
    return new URLSearchParams(window.location.search).get("player") || "";
}

let currentAccountId = "";

async function loadMastery(accountId) {
    const url = accountId ? `/api/mastery?player=${encodeURIComponent(accountId)}` : "/api/mastery";
    const res = await fetch(url);
    if (!res.ok) {
        document.getElementById("summary").innerHTML = `<h1>Could not load that player's profile.</h1>`;
        return;
    }
    const data = await res.json();
    currentAccountId = data.accountId;
    renderSummary(data);
    renderFarmingTargets(data.farmingTargets);
    renderItemGroups("categories", data.missingByCategory, renderMissingRow, showMissingModal, "Nothing missing — full completion!");
    renderItemGroups("in-progress-categories", data.inProgressByCategory, renderProgressRow, showProgressModal, "Nothing in progress right now.");
    await renderPlayerList(data.accountId);
}

function renderSummary(data) {
    const el = document.getElementById("summary");
    const founderBadge = data.founder > 0 ? ` &middot; Founder ${data.founder}` : "";

    const categoryItems = sortByCategoryOrder(Object.keys(data.categoryCompletion)).map(cat => {
        const c = data.categoryCompletion[cat];
        return `
            <div class="category-completion-item" data-category="${escapeHtml(cat)}">
                <span>${escapeHtml(cat)}</span>
                <span>${c.owned}/${c.total}</span>
            </div>
        `;
    }).join("");

    el.innerHTML = `
        <h1>${escapeHtml(data.displayName)} &mdash; Mastery Rank ${data.masteryRank}${founderBadge}</h1>
        <div class="summary-stats">
            <div><span class="label">Clan</span>${escapeHtml(data.guildName) || "&mdash;"}</div>
            <div><span class="label">Owned / Masterable</span>${data.totalOwned} / ${data.totalMasterable}</div>
            <div><span class="label">Completion</span>${data.completionPct}%</div>
            <div><span class="label">XP to MR ${data.masteryRank + 1}</span>${(data.masteryXpForNextRank - data.masteryXp).toLocaleString()}</div>
        </div>
        <div class="progress-bar">
            <div class="progress-bar-fill" style="width: ${data.completionPct}%"></div>
        </div>

        <div class="category-completion">${categoryItems}</div>
    `;

    el.querySelectorAll(".category-completion-item").forEach(row => {
        row.addEventListener("click", () => showCategoryModal(row.dataset.category, data.accountId));
    });
}

async function showCategoryModal(category, accountId) {
    const modal = document.getElementById("category-modal");
    const title = document.getElementById("category-modal-title");
    const body = document.getElementById("category-modal-body");

    title.textContent = category;
    body.innerHTML = "<p>Loading&hellip;</p>";
    modal.classList.remove("hidden");

    const url = accountId
        ? `/api/category/${encodeURIComponent(category)}?player=${encodeURIComponent(accountId)}`
        : `/api/category/${encodeURIComponent(category)}`;
    const res = await fetch(url);
    if (!res.ok) {
        body.innerHTML = "<p>Could not load this category.</p>";
        return;
    }
    const data = await res.json();

    body.innerHTML = data.items.map(item => {
        const icon = itemIconHtml(item.imageName, "item-icon");
        const crossTag = item.crossListed
            ? `<span class="cross-list-tag" title="Also appears in another category">2x</span>`
            : "";

        // Maxed items show no status at all — absence of a bar/rank implies
        // completion, same as an empty checklist row meaning "not started"
        // implies missing. Keeps the list from being cluttered with a wall
        // of identical 100% bars.
        const isMaxed = item.owned && item.rank >= item.maxRank;
        const status = isMaxed ? "" : `
            <span class="item-progress">
                <span class="item-mini-progress"><span class="item-mini-progress-fill" style="width: ${item.progressPct}%"></span></span>
                <span class="item-rank-badge">${item.owned ? `Rank ${item.rank}/${item.maxRank}` : "Missing"}</span>
            </span>
        `;

        return `
            <div class="category-item-row${item.owned ? "" : " missing"}">
                ${icon}
                <div class="category-item-info">
                    <span class="item-name${item.isPrime ? " prime" : ""}">${escapeHtml(item.name)}</span>
                    ${crossTag}
                </div>
                ${status}
            </div>
        `;
    }).join("");

    attachIconErrorFallback(body, "item-icon");
}

document.getElementById("category-modal-close").addEventListener("click", () => {
    document.getElementById("category-modal").classList.add("hidden");
});
document.getElementById("category-modal").addEventListener("click", (e) => {
    if (e.target.id === "category-modal") {
        e.target.classList.add("hidden");
    }
});

async function renderPlayerList(activeAccountId) {
    const res = await fetch("/api/users");
    const users = await res.json();
    const el = document.getElementById("player-list");
    el.innerHTML = users.map(u => `
        <a class="player-chip${u.accountId === activeAccountId ? " active" : ""}" href="?player=${encodeURIComponent(u.accountId)}">
            ${escapeHtml(u.displayName)}
        </a>
    `).join("");
}

function renderFarmingTargets(targets) {
    const container = document.getElementById("farming-targets");
    container.innerHTML = "";

    if (!targets || targets.length === 0) {
        container.innerHTML = `<p class="section-hint">Nothing to farm &mdash; everything's owned!</p>`;
        return;
    }

    targets.forEach(t => {
        const catEl = document.createElement("div");
        catEl.className = "category";
        catEl.innerHTML = `
            <div class="category-header">
                <span class="category-count">${t.itemCount}</span>
                <h2>${escapeHtml(t.location)}</h2>
            </div>
            <div class="category-items"></div>
        `;
        catEl.querySelector(".category-header").addEventListener("click", () => {
            catEl.classList.toggle("open");
        });

        const itemsEl = catEl.querySelector(".category-items");
        t.items.forEach(it => {
            const row = document.createElement("div");
            row.className = `item-row farming-item-row ${rarityClass(it.rarity)}`;
            row.innerHTML = `
                <span class="item-row-main">
                    ${itemIconHtml(it.imageName, "item-icon-sm")}
                    <span class="item-name">${escapeHtml(it.name)} <span class="item-component">${escapeHtml(it.component)}</span></span>
                </span>
                <span class="item-mastery-req">${it.chance != null ? `${it.chance}%` : ""}</span>
            `;
            attachIconErrorFallback(row, "item-icon-sm");
            itemsEl.appendChild(row);
        });

        container.appendChild(catEl);
    });
}

function renderMissingRow(item) {
    return `
        <span class="item-row-main">
            ${itemIconHtml(item.imageName, "item-icon-sm")}
            <span class="item-name${item.isPrime ? " prime" : ""}">${escapeHtml(item.name)}</span>
        </span>
        <span class="item-mastery-req">MR ${item.masteryReq}</span>
    `;
}

function renderProgressRow(item) {
    return `
        <span class="item-row-main">
            ${itemIconHtml(item.imageName, "item-icon-sm")}
            <span class="item-name${item.isPrime ? " prime" : ""}">${escapeHtml(item.name)}</span>
        </span>
        <span class="item-progress">
            <span class="item-mini-progress"><span class="item-mini-progress-fill" style="width: ${item.progressPct}%"></span></span>
            <span class="item-rank-badge">Rank ${item.rank}/${item.maxRank}</span>
        </span>
    `;
}

function renderItemGroups(containerId, byCategory, buildRowContent, onItemClick, emptyMessage) {
    const container = document.getElementById(containerId);
    container.innerHTML = "";

    const categories = sortByCategoryOrder(Object.keys(byCategory));
    if (categories.length === 0) {
        container.innerHTML = `<p class="section-hint">${escapeHtml(emptyMessage)}</p>`;
        return;
    }

    for (const cat of categories) {
        const items = byCategory[cat];
        const catEl = document.createElement("div");
        catEl.className = "category";
        catEl.innerHTML = `
            <div class="category-header">
                <span class="category-count">${items.length}</span>
                <h2>${escapeHtml(cat)}</h2>
            </div>
            <div class="category-items"></div>
        `;
        catEl.querySelector(".category-header").addEventListener("click", () => {
            catEl.classList.toggle("open");
        });

        const itemsEl = catEl.querySelector(".category-items");
        for (const item of items) {
            const row = document.createElement("div");
            row.className = "item-row";
            row.innerHTML = buildRowContent(item);
            row.addEventListener("click", () => onItemClick(item));
            attachIconErrorFallback(row, "item-icon-sm");
            itemsEl.appendChild(row);
        }

        container.appendChild(catEl);
    }
}

function renderSourceRows(sources) {
    return sources.map(s => `
        <div class="source-row">
            <div class="source-location">${escapeHtml(s.location) || "Unknown location"}</div>
            <div class="source-meta">${escapeHtml(s.component)} &middot; ${escapeHtml(s.rarity) || ""} &middot; ${s.chance != null ? s.chance + "%" : ""}</div>
        </div>
    `).join("");
}

function showMissingModal(item) {
    const modal = document.getElementById("item-modal");
    const body = document.getElementById("modal-body");
    const wikiUrl = safeWikiUrl(item.wikiaUrl);

    if (item.sources.length === 0) {
        let note;
        if (item.buildable) {
            note = `<p class="buildable-note">Purchasable / buildable from the in-game Market.</p>`;
        } else if (item.acquisitionNote) {
            note = `<p class="buildable-note">${escapeHtml(item.acquisitionNote)}</p>`;
        } else {
            note = `<p>No drop data available &mdash; check the wiki link below.</p>`;
        }
        body.innerHTML = `
            <h2>${escapeHtml(item.name)}</h2>
            <p class="item-mastery-req">Requires Mastery Rank ${item.masteryReq}</p>
            ${note}
            ${wikiUrl ? `<a class="wiki-link" href="${escapeHtml(wikiUrl)}" target="_blank" rel="noopener noreferrer">View on Warframe Wiki &rarr;</a>` : ""}
        `;
        modal.classList.remove("hidden");
        return;
    }

    // Group sources by component (Blueprint/Chassis/Neuroptics/Barrel/etc.)
    // so a multi-part item's drop locations can be filtered per part.
    const byComponent = new Map();
    for (const s of item.sources) {
        const comp = s.component || "Unknown";
        if (!byComponent.has(comp)) byComponent.set(comp, []);
        byComponent.get(comp).push(s);
    }
    const components = Array.from(byComponent.keys());

    const checkedComponents = new Set(item.checkedComponents || []);

    const filterChips = components.length > 1
        ? `<div class="component-filter">
               <button type="button" class="component-chip active" data-component="All">All</button>
               ${components.map(c => `
                   <span class="component-chip-group">
                       <input type="checkbox" class="component-checkbox" data-component="${escapeHtml(c)}"
                              ${checkedComponents.has(c) ? "checked" : ""} title="Mark as already found (removes it from Targeted Farming)" />
                       <button type="button" class="component-chip${checkedComponents.has(c) ? " checked-off" : ""}" data-component="${escapeHtml(c)}">${escapeHtml(c)}</button>
                   </span>
               `).join("")}
           </div>
           <p class="section-hint">Check a part off once you've found it &mdash; it'll drop out of Targeted Farming.</p>`
        : "";

    body.innerHTML = `
        <h2>${escapeHtml(item.name)}</h2>
        <p class="item-mastery-req">Requires Mastery Rank ${item.masteryReq}</p>
        ${filterChips}
        <div id="modal-sources-list">${renderSourceRows(item.sources)}</div>
        ${wikiUrl ? `<a class="wiki-link" href="${escapeHtml(wikiUrl)}" target="_blank" rel="noopener noreferrer">View on Warframe Wiki &rarr;</a>` : ""}
    `;

    body.querySelectorAll(".component-chip").forEach(chip => {
        chip.addEventListener("click", () => {
            body.querySelectorAll(".component-chip").forEach(c => c.classList.remove("active"));
            chip.classList.add("active");
            const filtered = chip.dataset.component === "All" ? item.sources : byComponent.get(chip.dataset.component);
            document.getElementById("modal-sources-list").innerHTML = renderSourceRows(filtered);
        });
    });

    body.querySelectorAll(".component-checkbox").forEach(checkbox => {
        checkbox.addEventListener("change", async () => {
            const component = checkbox.dataset.component;
            const wasChecked = checkbox.checked;
            checkbox.disabled = true;

            const res = await fetch("/api/checked-parts", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    accountId: currentAccountId,
                    uniqueName: item.uniqueName,
                    component,
                    checked: wasChecked,
                }),
            });

            checkbox.disabled = false;
            if (!res.ok) {
                checkbox.checked = !wasChecked;
                return;
            }

            const result = await res.json();
            item.checkedComponents = result.checkedComponents;
            const chipBtn = body.querySelector(`.component-chip[data-component="${CSS.escape(component)}"]`);
            if (chipBtn) chipBtn.classList.toggle("checked-off", wasChecked);

            // Refresh the rest of the page (Targeted Farming in particular)
            // in the background without closing this modal.
            loadMastery(currentAccountId);
        });
    });

    modal.classList.remove("hidden");
}

function showProgressModal(item) {
    const modal = document.getElementById("item-modal");
    const body = document.getElementById("modal-body");

    body.innerHTML = `
        <h2>${escapeHtml(item.name)}</h2>
        <p class="item-mastery-req">Rank ${item.rank} / ${item.maxRank} &middot; ${item.progressPct}% to max</p>
        <div class="progress-bar">
            <div class="progress-bar-fill" style="width: ${item.progressPct}%"></div>
        </div>
        <p class="source-meta">${item.xp.toLocaleString()} / ${item.maxXp.toLocaleString()} affinity</p>
    `;
    modal.classList.remove("hidden");
}

document.getElementById("modal-close").addEventListener("click", () => {
    document.getElementById("item-modal").classList.add("hidden");
});
document.getElementById("item-modal").addEventListener("click", (e) => {
    if (e.target.id === "item-modal") {
        e.target.classList.add("hidden");
    }
});

async function addPlayer(accountId) {
    const errorEl = document.getElementById("player-form-error");
    errorEl.classList.add("hidden");

    if (!/^[0-9a-fA-F]{24}$/.test(accountId)) {
        errorEl.textContent = "That doesn't look like a valid 24-character account ID.";
        errorEl.classList.remove("hidden");
        return false;
    }

    const res = await fetch("/api/users", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accountId }),
    });

    if (!res.ok) {
        const msg = await res.text();
        errorEl.textContent = `Could not add that player: ${msg}`;
        errorEl.classList.remove("hidden");
        return false;
    }

    const user = await res.json();
    window.history.pushState({}, "", `?player=${encodeURIComponent(user.accountId)}`);
    loadMastery(user.accountId);
    return true;
}

document.getElementById("add-player-btn").addEventListener("click", async () => {
    const input = document.getElementById("new-account-id");
    if (await addPlayer(input.value.trim())) {
        input.value = "";
    }
});

// Built from the current origin so the bookmarklet targets whichever
// instance is serving this page, rather than a hardcoded domain.
const bookmarkletLink = document.getElementById("bookmarklet-link");
bookmarkletLink.href = "javascript:" + encodeURIComponent(
    "(function(){fetch('https://www.warframe.com/api/user-data',{credentials:'same-origin'})" +
    ".then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json();})" +
    ".then(function(d){if(!d||!d.user_id)throw new Error('no user_id');" +
    "window.location.href='" + window.location.origin + "/?prefill='+encodeURIComponent(d.user_id);})" +
    ".catch(function(e){alert('Could not get your Warframe account ID (are you logged into warframe.com?): '+e.message);});})();"
);

const playerPanel = document.getElementById("player-panel");
const playerPanelToggle = document.getElementById("player-panel-toggle");

playerPanelToggle.addEventListener("click", (e) => {
    e.stopPropagation();
    playerPanel.classList.toggle("hidden");
});

playerPanel.addEventListener("click", (e) => {
    e.stopPropagation();
});

document.addEventListener("click", () => {
    playerPanel.classList.add("hidden");
});

function handlePrefill() {
    const params = new URLSearchParams(window.location.search);
    const prefillId = params.get("prefill");
    if (!prefillId) return;

    // Strip the param so a page refresh doesn't keep re-triggering this.
    params.delete("prefill");
    const cleanQuery = params.toString();
    window.history.replaceState({}, "", window.location.pathname + (cleanQuery ? `?${cleanQuery}` : ""));

    if (!/^[0-9a-fA-F]{24}$/.test(prefillId)) return;

    document.getElementById("new-account-id").value = prefillId;
    playerPanel.classList.remove("hidden");
}

handlePrefill();
loadMastery(currentPlayerId());
