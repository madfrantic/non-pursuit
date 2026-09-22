"""
Turns a pile of "this handle exists here" results into a scored claim about
which accounts belong to one person.

Existence and ownership are different questions, and every tool this borrows
from answers only the first. `github.com/jsmith` existing is a fact about
GitHub. That it is *your* GitHub is an inference, and the strength of that
inference is what a deletion demand actually rests on -- a broker that
receives a demand naming an account the requester cannot connect to
themselves has a one-line rejection available.

So results are nodes in a graph and corroboration is edges, and the score is
the graph's answer rather than the scanner's.

WHY LOG-ODDS RATHER THAN A POINT TALLY

The obvious design -- start at 50, add 10 for a name match, add 20 for a
backlink -- has two failure modes that matter here. It runs off the end of
the scale (four signals and everything is 100% certain), and it treats the
fifth piece of the same kind of evidence as worth as much as the first.

Accumulating in log-odds and reading the score back through a logistic fixes
both without a clamp: evidence adds linearly in log-odds space and
compresses automatically towards 0 and 100, so the score approaches
certainty and never reaches it. Independent-ish evidence is what log-odds
addition assumes, which is why same-type evidence is damped geometrically
before it is added (see _accumulate) -- three people with your name in three
places is not three independent confirmations, it is one fact observed three
times.

WHY HANDLE RARITY IS A PRIOR AND NOT A BONUS

This is the correction that separates a useful score from a confident wrong
one. "jsmith" exists on 400 platforms and almost none of them are yours;
"jsmith_1987_hxk" exists on nine and probably all nine are. Sherlock and
Maigret report those two cases identically. Rarity enters here as a shift to
the *prior* -- before any evidence -- because it changes how likely the
account was to be yours in the first place, not how convincing the evidence
about it is. A common handle can still reach a high score, but it has to
earn it with real corroboration.

WHY SCORES PROPAGATE

A confirmed GitHub that links to a Twitter raises that Twitter. The raised
Twitter is then better evidence for whatever *it* links to. That is the
escalation the design calls for, and it is also how a feedback loop starts,
so propagation is bounded: a fixed number of rounds, each edge's weight
scaled by the current confidence of the node it comes from, and a damping
factor below 1. Scores rise towards a fixed point instead of chasing each
other upward.

Every score carries the evidence that produced it. A number a user cannot
audit is not usable in a compliance record.
"""
import math
import re
from dataclasses import dataclass, field

# Verdict strings from footprint_scanner, restated rather than imported so
# this module stays usable against any scanner that emits the same words.
CONFIRMED = "CONFIRMED"
POSSIBLE = "POSSIBLE"

NODE_ACCOUNT = "account"
NODE_EMAIL = "email"
NODE_NAME = "name"
NODE_LOCATION = "location"

# Prior probability that an account is the target's, before corroboration,
# given only what the existence check said. CONFIRMED sits just above even:
# the account demonstrably exists, which is genuine evidence, but a bare
# handle collision is common enough that existence alone is not most of the
# way to ownership.
VERDICT_PRIOR = {
    CONFIRMED: 0.55,
    POSSIBLE: 0.22,
}
DEFAULT_PRIOR = 0.15

# Evidence weights in log-odds. Calibrated by how hard each signal is to
# produce by coincidence rather than by how impressive it looks:
#
#   A reciprocal backlink requires the account holder to have edited two
#   different profiles to point at each other. Nothing else available
#   without authenticating is close.
#
#   A name match is weak on its own and stays weak. "David Chen" matching
#   "David Chen" is a real signal in a rare-name case and nearly worthless
#   in a common-name one, and this scorer cannot tell those apart, so the
#   weight is set for the common case.
EVIDENCE_WEIGHTS = {
    "seed_declared": 3.5,        # the user said "this one is mine"
    "backlink_reciprocal": 2.2,  # A -> B and B -> A
    "email_match_seed": 1.9,     # page carries an address the user gave us
    "backlink_inbound": 1.3,     # a corroborated account links here
    "email_match_shared": 1.1,   # two accounts share a non-seed address
    "name_match_exact": 0.9,
    "backlink_outbound": 0.7,    # this account links at a corroborated one
    "location_match": 0.6,
    "name_match_partial": 0.35,
    "handle_exact_seed": 0.5,    # handle is exactly one the user gave us
}

# Same-type evidence is damped geometrically: the n-th item of a type is
# worth DAMPING^(n-1) of the first. 0.45 means a second name match adds
# under half of the first and a fifth adds almost nothing.
DAMPING = 0.45

# Propagation rounds. Three is enough for a chain (seed -> A -> B -> C) to
# carry through; more mostly re-amplifies what round three already found.
PROPAGATION_ROUNDS = 3
# Each round's contribution is scaled by this, so successive rounds add
# progressively less and the fixed point is reached rather than overshot.
PROPAGATION_DAMPING = 0.6

# Bands the UI and the remediation gate read. High is the threshold above
# which this engine is willing to auto-generate a deletion demand naming the
# account: below it, a demand risks asserting an identity link that is not
# there, which is worse for the requester than sending nothing.
BAND_HIGH = 75
BAND_MEDIUM = 45

_WORD_RE = re.compile(r"[a-z0-9]+")
# Handle fragments common enough to carry no identifying weight on their own.
_COMMON_TOKENS = frozenset({
    "the", "real", "official", "user", "admin", "test", "dev", "me", "my",
    "im", "its", "mr", "mrs", "ms", "dr", "xx", "xo", "yt", "tv", "hq",
    "john", "jane", "mike", "dave", "chris", "alex", "sam", "max", "nick",
    "smith", "jones", "brown", "lee", "chen", "kim", "singh", "garcia",
})


def logit(probability: float) -> float:
    probability = min(max(probability, 1e-6), 1 - 1e-6)
    return math.log(probability / (1 - probability))


def sigmoid(value: float) -> float:
    if value < 0:
        exponential = math.exp(value)
        return exponential / (1 + exponential)
    return 1 / (1 + math.exp(-value))


def normalize_handle(handle: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(handle or "").lower())


def normalize_name(name: str) -> str:
    return " ".join(_WORD_RE.findall(str(name or "").lower()))


def normalize_location(location: str) -> str:
    """Reduce a location to its comparable tokens.

    'Austin, TX', 'austin texas' and 'Austin, Texas, USA' should compare
    equal. Deliberately crude -- no geocoding, no gazetteer. A location
    match is worth 0.6 log-odds here, which does not justify a network
    dependency or the failure modes of getting it wrong.
    """
    tokens = _WORD_RE.findall(str(location or "").lower())
    drop = {"usa", "us", "united", "states", "uk", "the"}
    return " ".join(t for t in tokens if t not in drop)


def handle_rarity(handle: str) -> float:
    """How distinctive a handle is, on 0..1.

    Three things make a handle unlikely to collide: length, character
    variety (digits and separators mixed with letters), and not being a
    bare common word or first name. The output is deliberately smooth --
    it shifts a prior, so a cliff edge between "common" and "rare" would
    put two nearly identical handles in different confidence bands.
    """
    raw = str(handle or "").strip()
    if not raw:
        return 0.0

    normalized = normalize_handle(raw)
    if not normalized:
        return 0.0

    # Length: 3 chars is near-certainly shared, 16+ is near-certainly not.
    length_score = min(max((len(normalized) - 3) / 13.0, 0.0), 1.0)

    # Variety: letters plus digits plus a separator is far less likely to
    # be someone else's than a bare lowercase word.
    classes = sum((
        any(c.isalpha() for c in normalized),
        any(c.isdigit() for c in normalized),
        bool(re.search(r"[._\-]", raw)),
        bool(re.search(r"[A-Z]", raw)) and not raw.isupper(),
    ))
    variety_score = classes / 4.0

    # A handle that is exactly one common word or first name is the case
    # this whole function exists to catch.
    dictionary_penalty = 0.6 if normalized in _COMMON_TOKENS else 0.0
    tokens = set(re.split(r"[._\-]+", raw.lower())) - {""}
    if tokens and tokens <= _COMMON_TOKENS:
        dictionary_penalty = max(dictionary_penalty, 0.35)

    rarity = 0.6 * length_score + 0.4 * variety_score - dictionary_penalty
    return min(max(rarity, 0.0), 1.0)


def rarity_prior_shift(handle: str) -> float:
    """Log-odds shift applied to an account's prior for handle rarity.

    Spans roughly -1.2 (a bare common first name) to +1.2 (a long mixed
    handle), which is enough to move a bare CONFIRMED across a band on its
    own -- as it should, since that is genuinely the difference between
    "someone called john" and "this specific string".
    """
    return (handle_rarity(handle) - 0.5) * 2.4


@dataclass
class Evidence:
    """One reason a node's score moved, kept for the audit trail."""

    kind: str
    detail: str
    weight: float
    source: str = ""

    def as_dict(self) -> dict:
        return {"kind": self.kind, "detail": self.detail,
                "weight": round(self.weight, 3), "source": self.source}


@dataclass
class Node:
    key: str
    node_type: str
    platform: str = ""
    handle: str = ""
    url: str = ""
    verdict: str = ""
    metadata: dict = field(default_factory=dict)
    evidence: list = field(default_factory=list)
    prior: float = DEFAULT_PRIOR
    score: float = 0.0
    is_seed: bool = False


def account_key(platform: str, handle: str) -> str:
    return f"{NODE_ACCOUNT}:{normalize_handle(platform)}:{normalize_handle(handle)}"


class IdentityGraph:
    """Accounts, the identifiers that tie them together, and a score per
    account for "this belongs to the target"."""

    def __init__(self):
        self.nodes: dict[str, Node] = {}
        # (source_key, target_key, kind) -> Evidence, so the same
        # corroboration observed twice contributes once.
        self.edges: dict[tuple, Evidence] = {}
        self.seed = {"emails": set(), "names": set(), "locations": set(),
                     "handles": set()}

    # -- construction ------------------------------------------------------

    def add_seed(self, *, emails=None, names=None, locations=None,
                 handles=None, accounts=None) -> None:
        """Register what the user says is theirs.

        Seed values are the anchor the whole graph is scored against -- an
        account corroborates towards *these*, not towards the average of
        everything found. Without a seed the graph still runs, but every
        score rests on handle rarity and inter-account links alone, which
        is a much weaker claim and one the report should say out loud.
        """
        for email in emails or []:
            if email:
                self.seed["emails"].add(email.strip().lower())
        for name in names or []:
            if normalize_name(name):
                self.seed["names"].add(normalize_name(name))
        for location in locations or []:
            if normalize_location(location):
                self.seed["locations"].add(normalize_location(location))
        for handle in handles or []:
            if normalize_handle(handle):
                self.seed["handles"].add(normalize_handle(handle))

        for platform, handle in accounts or []:
            node = self.add_account(platform, handle, verdict=CONFIRMED)
            node.is_seed = True

    def add_account(self, platform: str, handle: str, *, url: str = "",
                    verdict: str = CONFIRMED, metadata: dict | None = None) -> Node:
        """Add or update one discovered account.

        Idempotent by (platform, handle): re-adding merges metadata rather
        than replacing the node, so a scan result and a link resolved from
        another page describe the same node instead of competing.
        """
        key = account_key(platform, handle)
        node = self.nodes.get(key)
        if node is None:
            node = Node(key=key, node_type=NODE_ACCOUNT, platform=platform,
                        handle=handle, url=url, verdict=verdict)
            self.nodes[key] = node
        node.url = node.url or url
        if verdict == CONFIRMED:
            node.verdict = CONFIRMED
        elif not node.verdict:
            node.verdict = verdict
        if metadata:
            for field_name, value in metadata.items():
                if value and not node.metadata.get(field_name):
                    node.metadata[field_name] = value
        return node

    def _add_edge(self, source: str, target: str, kind: str, detail: str) -> None:
        weight = EVIDENCE_WEIGHTS.get(kind, 0.0)
        if not weight:
            return
        self.edges.setdefault((source, target, kind),
                              Evidence(kind=kind, detail=detail, weight=weight,
                                       source=source))

    # -- evidence gathering -----------------------------------------------

    def link_accounts(self, source_platform: str, source_handle: str,
                      target_platform: str, target_handle: str) -> None:
        """Record that the source account's page linked to the target's.

        Reciprocity is detected here rather than at scoring time: once both
        directions exist, the two one-way edges are replaced by a single
        reciprocal edge in each direction. Keeping all three would count the
        same mutual link three times.
        """
        source = account_key(source_platform, source_handle)
        target = account_key(target_platform, target_handle)
        if source == target:
            return

        self._add_edge(target, source, "backlink_inbound",
                       f"{source_platform} profile links to {target_platform}")
        self._add_edge(source, target, "backlink_outbound",
                       f"links to {target_platform} profile")

        reverse_inbound = (source, target, "backlink_inbound")
        if reverse_inbound in self.edges:
            for a, b in ((source, target), (target, source)):
                self.edges.pop((a, b, "backlink_inbound"), None)
                self.edges.pop((a, b, "backlink_outbound"), None)
            detail = f"{source_platform} and {target_platform} link to each other"
            self._add_edge(target, source, "backlink_reciprocal", detail)
            self._add_edge(source, target, "backlink_reciprocal", detail)

    def infer_attribute_evidence(self) -> None:
        """Compare every account's extracted metadata against the seed and
        against the other accounts, and record what matches.

        Run once after all accounts and links are in. Shared-email matching
        between two non-seed accounts is deliberately weaker than a seed
        match: it establishes that two accounts belong to the same person,
        which is only useful once something else establishes that the person
        is the target.
        """
        by_email: dict[str, list] = {}

        for node in list(self.nodes.values()):
            if node.node_type != NODE_ACCOUNT:
                continue

            if node.is_seed:
                node.evidence.append(Evidence(
                    "seed_declared", "declared by the user as their account",
                    EVIDENCE_WEIGHTS["seed_declared"]))

            if normalize_handle(node.handle) in self.seed["handles"]:
                node.evidence.append(Evidence(
                    "handle_exact_seed", f"handle matches the searched handle "
                    f"'{node.handle}'", EVIDENCE_WEIGHTS["handle_exact_seed"]))

            for email in node.metadata.get("emails") or []:
                lowered = email.lower()
                by_email.setdefault(lowered, []).append(node)
                if lowered in self.seed["emails"]:
                    node.evidence.append(Evidence(
                        "email_match_seed", f"page carries {email}",
                        EVIDENCE_WEIGHTS["email_match_seed"]))

            found_name = normalize_name(node.metadata.get("name", ""))
            if found_name:
                if found_name in self.seed["names"]:
                    node.evidence.append(Evidence(
                        "name_match_exact", f"display name '{node.metadata['name']}'",
                        EVIDENCE_WEIGHTS["name_match_exact"]))
                elif any(self._names_overlap(found_name, seed)
                         for seed in self.seed["names"]):
                    node.evidence.append(Evidence(
                        "name_match_partial", f"display name '{node.metadata['name']}'",
                        EVIDENCE_WEIGHTS["name_match_partial"]))

            found_location = normalize_location(node.metadata.get("location", ""))
            if found_location and any(
                self._locations_overlap(found_location, seed)
                for seed in self.seed["locations"]
            ):
                node.evidence.append(Evidence(
                    "location_match", f"location '{node.metadata['location']}'",
                    EVIDENCE_WEIGHTS["location_match"]))

        for email, nodes in by_email.items():
            if len(nodes) < 2 or email in self.seed["emails"]:
                continue
            for node in nodes:
                others = ", ".join(sorted({n.platform for n in nodes if n is not node}))
                node.evidence.append(Evidence(
                    "email_match_shared",
                    f"shares {email} with {others}",
                    EVIDENCE_WEIGHTS["email_match_shared"]))

    @staticmethod
    def _names_overlap(found: str, seed: str) -> bool:
        """Partial name match: a shared token of real length.

        Requires 3+ characters so initials and particles ('de', 'van') can't
        carry a match on their own.
        """
        found_tokens = {t for t in found.split() if len(t) >= 3}
        seed_tokens = {t for t in seed.split() if len(t) >= 3}
        return bool(found_tokens & seed_tokens)

    @staticmethod
    def _locations_overlap(found: str, seed: str) -> bool:
        found_tokens = {t for t in found.split() if len(t) >= 3}
        seed_tokens = {t for t in seed.split() if len(t) >= 3}
        return bool(found_tokens & seed_tokens)

    # -- scoring -----------------------------------------------------------

    @staticmethod
    def _accumulate(evidence: list) -> float:
        """Sum evidence in log-odds with per-type geometric damping.

        Grouping by kind first is what stops six weak same-kind signals
        outweighing one reciprocal backlink. Within a kind the items are
        sorted strongest-first so the damping always discounts the weaker
        duplicates, never the strongest instance.
        """
        by_kind: dict[str, list] = {}
        for item in evidence:
            by_kind.setdefault(item.kind, []).append(item)

        total = 0.0
        for items in by_kind.values():
            for index, item in enumerate(sorted(items, key=lambda e: -e.weight)):
                total += item.weight * (DAMPING ** index)
        return total

    def _base_prior(self, node: Node) -> float:
        prior = VERDICT_PRIOR.get(node.verdict, DEFAULT_PRIOR)
        shifted = logit(prior) + rarity_prior_shift(node.handle)
        return sigmoid(shifted)

    def score(self) -> dict:
        """Score every account and return the full report.

        Runs attribute inference, then alternates scoring and propagation so
        that a node raised by its own evidence can lift its neighbours in the
        next round.
        """
        self.infer_attribute_evidence()

        accounts = [n for n in self.nodes.values() if n.node_type == NODE_ACCOUNT]
        for node in accounts:
            node.prior = self._base_prior(node)
            node.score = sigmoid(logit(node.prior) + self._accumulate(node.evidence)) * 100

        propagated: dict[str, list] = {node.key: [] for node in accounts}
        for round_index in range(PROPAGATION_ROUNDS):
            round_scale = PROPAGATION_DAMPING ** round_index
            fresh: dict[str, list] = {node.key: [] for node in accounts}

            for (source_key, target_key, kind), evidence in self.edges.items():
                source = self.nodes.get(source_key)
                target = self.nodes.get(target_key)
                if source is None or target is None:
                    continue
                # An edge is only as good as the node it comes from. A link
                # from an account we are 20% sure about is 20% of a link.
                confidence = source.score / 100.0
                weight = evidence.weight * confidence * round_scale
                if weight < 0.01:
                    continue
                fresh[target_key].append(Evidence(
                    kind=evidence.kind,
                    detail=f"{evidence.detail} (source confidence {source.score:.0f}%)",
                    weight=weight,
                    source=source.platform or source_key,
                ))

            for node in accounts:
                propagated[node.key] = fresh[node.key]
                node.score = sigmoid(
                    logit(node.prior)
                    + self._accumulate(node.evidence + propagated[node.key])
                ) * 100

        results = []
        for node in sorted(accounts, key=lambda n: -n.score):
            trail = node.evidence + propagated.get(node.key, [])
            results.append({
                "platform": node.platform,
                "handle": node.handle,
                "url": node.url,
                "verdict": node.verdict,
                "confidence": round(node.score, 1),
                "band": band(node.score),
                "prior": round(node.prior * 100, 1),
                "handle_rarity": round(handle_rarity(node.handle), 3),
                "is_seed": node.is_seed,
                "metadata": {k: v for k, v in node.metadata.items() if v},
                "evidence": [e.as_dict() for e in sorted(trail, key=lambda e: -e.weight)],
            })

        return {
            "seed": {k: sorted(v) for k, v in self.seed.items()},
            "seeded": bool(any(self.seed.values())),
            "accounts": results,
            "summary": summarize(results),
        }


def band(score: float) -> str:
    if score >= BAND_HIGH:
        return "high"
    if score >= BAND_MEDIUM:
        return "medium"
    return "low"


def summarize(results: list) -> dict:
    counts = {"high": 0, "medium": 0, "low": 0}
    for row in results:
        counts[row["band"]] = counts.get(row["band"], 0) + 1
    return {"total": len(results), "by_band": counts}


def build_from_scan(scan_results: list, resolvers: list, *,
                    seed_handles=None, seed_emails=None, seed_names=None,
                    seed_locations=None, seed_accounts=None,
                    include_verdicts=(CONFIRMED, POSSIBLE)) -> IdentityGraph:
    """Assemble a scored graph from recon_engine output.

    `resolvers` comes from metadata_extractor.build_link_resolver. Links that
    resolve to a platform not otherwise scanned still create a node -- an
    account found only because another profile pointed at it is often the
    most interesting result of a scan, since nothing was searching for it.
    """
    from metadata_extractor import resolve_link  # local: avoids a cycle

    graph = IdentityGraph()
    graph.add_seed(handles=seed_handles, emails=seed_emails, names=seed_names,
                   locations=seed_locations, accounts=seed_accounts)

    for row in scan_results:
        if row.get("verdict") not in include_verdicts:
            continue
        metadata = row.get("metadata") or {}
        graph.add_account(
            row.get("platform", ""),
            row.get("handle", "") or row.get("target_identifier", ""),
            url=row.get("url") or row.get("profile_url", ""),
            verdict=row.get("verdict", ""),
            metadata=metadata,
        )

    for row in scan_results:
        if row.get("verdict") not in include_verdicts:
            continue
        source_platform = row.get("platform", "")
        source_handle = row.get("handle", "") or row.get("target_identifier", "")
        for url in (row.get("metadata") or {}).get("links") or []:
            resolved = resolve_link(url, resolvers)
            if not resolved:
                continue
            target_platform, target_handle = resolved
            graph.add_account(target_platform, target_handle, url=url,
                              verdict=POSSIBLE)
            graph.link_accounts(source_platform, source_handle,
                                target_platform, target_handle)
    return graph
