"""Client for the Warframe mastery tracker (WARFRAME_TRACKER_URL), plus
fuzzy matching of OCR'd part names onto its still-missing items."""
import difflib
import json
import os
import re
import time
import urllib.request

# Where the tracker is served (the same origin as its web page).
TRACKER_URL = os.environ.get("WARFRAME_TRACKER_URL", "http://127.0.0.1:8420")
MATCH_THRESHOLD = 0.88
# The tracker allows 30 req/min per IP; stay comfortably under it.
MIN_SECONDS_BETWEEN_REQUESTS = 2.5


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9&]", "", s.lower())


class Tracker:
    def __init__(self, base_url: str = TRACKER_URL):
        self.base_url = base_url
        self._last_request = 0.0
        self.account_id = None
        self.labels = {}  # squashed in-game label -> (uniqueName, component, display label, item name)
        self.found = {}  # (uniqueName, component) -> count marked found
        self.required = {}  # (uniqueName, component) -> count the recipe needs
        self.missing = {}  # squashed item name -> (uniqueName, name) for every missing item

    def _request(self, path: str, body: dict | None = None) -> dict:
        wait = self._last_request + MIN_SECONDS_BETWEEN_REQUESTS - time.time()
        if wait > 0:
            time.sleep(wait)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url + path, data=data,
            headers={"User-Agent": "warframe-reader/1.0", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        finally:
            self._last_request = time.time()

    def refresh(self):
        """Reload missing items and their already-checked components."""
        payload = self._request("/api/mastery")
        self.account_id = payload["accountId"]
        self.labels.clear()
        self.found.clear()
        self.required.clear()
        self.missing.clear()
        for items in payload["missingByCategory"].values():
            for item in items:
                self.missing[_squash(item["name"])] = (item["uniqueName"], item["name"])
                for comp, n in item.get("foundCounts", {}).items():
                    self.found[(item["uniqueName"], comp)] = n
                for comp in {s["component"] for s in item["sources"]}:
                    self.required[(item["uniqueName"], comp)] = item.get("componentCounts", {}).get(comp, 1)
                    # The game labels parts "<Item> <Part>", and Warframe
                    # parts as "<Item> <Part> Blueprint" before crafting.
                    for label in (f"{item['name']} {comp}", f"{item['name']} {comp} Blueprint"):
                        self.labels.setdefault(_squash(label), (item["uniqueName"], comp, f"{item['name']} {comp}", item["name"]))

    def match(self, ocr_name: str):
        """Best (uniqueName, component, display) for an OCR'd tile, or None."""
        key = _squash(ocr_name)
        if key in self.labels:
            return self.labels[key]
        # The item name has to match on its own too: "Baruuk Prime Chassis
        # Blueprint" otherwise scores high against "Xaku Prime Chassis
        # Blueprint" on the shared suffix alone.
        # Prime-ness must agree too: the Miscellaneous tab lists non-prime
        # "Akarius Blueprint", which scores 0.86 against "Akarius Prime
        # Blueprint" -- one OCR slip away from a false match.
        is_prime = "prime" in key
        for close in difflib.get_close_matches(key, self.labels.keys(), n=5, cutoff=MATCH_THRESHOLD):
            item_key = _squash(self.labels[close][3])
            if ("prime" in item_key) != is_prime:
                continue
            if difflib.SequenceMatcher(None, key[:len(item_key)], item_key).ratio() >= MATCH_THRESHOLD:
                return self.labels[close]
        return None

    def match_item(self, ocr_name: str):
        """(uniqueName, name) of the missing item an equipment tile shows,
        or None. Prime-ness must agree so "Lavos" can't match "Lavos Prime"."""
        key = _squash(ocr_name)
        if key in self.missing:
            return self.missing[key]
        # Kuva/Tenet weapons print their progenitor's name above the weapon
        # name ("Zol Fosli Tenet" / "Tenet Exec"), so also try the tile text
        # with leading words dropped -- exact matches only for those.
        words = ocr_name.split()
        for i in range(1, len(words)):
            suffix = _squash(" ".join(words[i:]))
            if suffix in self.missing:
                return self.missing[suffix]
        # Fuzzy only for one-character OCR slips ("Límbo" squashes to
        # "lmbo"). Item names are short, so anything looser pairs different
        # items: "Magnus Prime" scored 0.92 against "Akmagnus Prime" and
        # "Latron Prime" 0.90 against "Lato Prime".
        is_prime = "prime" in key
        for close in difflib.get_close_matches(key, self.missing.keys(), n=3, cutoff=0.9):
            if ("prime" in close) == is_prime and abs(len(close) - len(key)) <= 1:
                return self.missing[close]
        return None

    def set_owned(self, unique_name: str):
        self._request("/api/owned-items", {"accountId": self.account_id, "uniqueName": unique_name})
        self.missing = {k: v for k, v in self.missing.items() if v[0] != unique_name}

    def load_arcanes(self):
        """Arcane catalog + what's recorded, for matching arcane tiles."""
        payload = self._request("/api/arcanes")
        self.account_id = self.account_id or payload["accountId"]
        self.arcanes = {_squash(a["name"]): a for a in payload["arcanes"]}

    def match_arcane(self, ocr_name: str):
        key = _squash(ocr_name)
        if key in self.arcanes:
            return self.arcanes[key]
        for close in difflib.get_close_matches(key, self.arcanes.keys(), n=1, cutoff=0.9):
            if abs(len(close) - len(key)) <= 1:
                return self.arcanes[close]
        return None

    def set_arcanes(self, updates: list) -> dict:
        return self._request("/api/arcanes", {"accountId": self.account_id, "updates": updates})["updated"]

    def set_found(self, unique_name: str, component: str, count: int):
        result = self._request("/api/checked-parts", {
            "accountId": self.account_id,
            "uniqueName": unique_name,
            "component": component,
            "count": count,
        })
        self.found[(unique_name, component)] = result["foundCounts"].get(component, 0)
