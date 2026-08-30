"""Sync client for the Warcraft Logs v2 GraphQL API (OAuth client-credentials)."""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

from lootcouncil.config import Config

logger = logging.getLogger("lootcouncil")

OAUTH_URL = "https://www.warcraftlogs.com/oauth/token"
API_URL = "https://www.warcraftlogs.com/api/v2/client"
TIMEOUT = 60

ZONE_RANKINGS_QUERY = """
query($name: String!, $server: String!, $region: String!, $zone: Int!) {
  characterData {
    character(name: $name, serverSlug: $server, serverRegion: $region) {
      zoneRankings(zoneID: $zone)
    }
  }
}
"""

RECENT_REPORTS_QUERY = """
query($name: String!, $server: String!, $region: String!) {
  characterData {
    character(name: $name, serverSlug: $server, serverRegion: $region) {
      recentReports(limit: 25) {
        data { code startTime zone { name } }
      }
    }
  }
}
"""

FIGHTS_QUERY = """
query($code: String!) {
  reportData {
    report(code: $code) {
      fights(killType: Encounters) { id name encounterID difficulty kill }
    }
  }
}
"""

TABLE_QUERY = """
query($code: String!, $fightIDs: [Int]!, $dataType: TableDataType!) {
  reportData {
    report(code: $code) {
      table(fightIDs: $fightIDs, dataType: $dataType)
    }
  }
}
"""


class WarcraftLogsError(RuntimeError):
    """Raised on OAuth failure or a GraphQL error response."""


@dataclass(frozen=True)
class ParseData:
    average: float = 0.0
    per_encounter: Dict[int, float] = field(default_factory=dict)
    metric: str = ""


@dataclass(frozen=True)
class ReportRef:
    code: str
    zone_name: str
    start_time: int


@dataclass(frozen=True)
class Fight:
    id: int
    name: str
    encounter_id: int
    difficulty: int
    kill: bool


def _dig(data: Any, *keys: str) -> Any:
    """Walk nested dicts, returning None the moment anything is missing or not a dict."""
    node = data
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _parse_zone_rankings(payload: Any) -> ParseData:
    rankings = _dig(payload, "data", "characterData", "character", "zoneRankings")
    if not isinstance(rankings, dict):
        return ParseData()
    per_encounter: Dict[int, float] = {}
    for row in rankings.get("rankings") or []:
        pct = row.get("rankPercent")
        enc_id = _dig(row, "encounter", "id")
        if pct is None or enc_id is None:
            continue
        per_encounter[int(enc_id)] = float(pct)
    average = sum(per_encounter.values()) / len(per_encounter) if per_encounter else 0.0
    return ParseData(average=average, per_encounter=per_encounter, metric=rankings.get("metric") or "")


def _parse_recent_reports(payload: Any) -> List[ReportRef]:
    rows = _dig(payload, "data", "characterData", "character", "recentReports", "data") or []
    out: List[ReportRef] = []
    for row in rows:
        code = row.get("code")
        if not code:
            continue
        out.append(
            ReportRef(
                code=code,
                zone_name=(_dig(row, "zone", "name") or ""),
                start_time=int(row.get("startTime") or 0),
            )
        )
    return out


def _parse_fights(payload: Any, difficulty: int) -> List[Fight]:
    rows = _dig(payload, "data", "reportData", "report", "fights") or []
    out: List[Fight] = []
    for row in rows:
        encounter_id = int(row.get("encounterID") or 0)
        if not encounter_id:  # trash / non-encounter
            continue
        if int(row.get("difficulty") or 0) != int(difficulty):
            continue
        out.append(
            Fight(
                id=int(row["id"]),
                name=row.get("name", ""),
                encounter_id=encounter_id,
                difficulty=int(row.get("difficulty") or 0),
                kill=bool(row.get("kill")),
            )
        )
    return out


def _parse_table_entries(payload: Any) -> List[dict]:
    table = _dig(payload, "data", "reportData", "report", "table", "data")
    if isinstance(table, list):
        return table
    if isinstance(table, dict):
        return table.get("entries") or []
    return []


class WarcraftLogsClient:
    """Holds the OAuth token and issues GraphQL queries.

    The 3,600 points/hour budget is only ever touched during season aggregation,
    which is cached — normal loot lookups make zero calls.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._token: Optional[str] = None
        self._token_expiry: float = 0.0  # monotonic seconds

    def ensure_token(self) -> str:
        if self._token and time.monotonic() < self._token_expiry:
            return self._token
        cid, secret = self._cfg.wcl_client_id, self._cfg.wcl_client_secret
        if not cid or not secret:
            raise WarcraftLogsError(
                "WARCRAFT_LOGS_API_CLIENT_ID / WARCRAFT_LOGS_API_CLIENT_SECRET are not set"
            )
        resp = requests.post(
            OAUTH_URL, data={"grant_type": "client_credentials"}, auth=(cid, secret), timeout=TIMEOUT
        )
        if resp.status_code != 200:
            raise WarcraftLogsError(f"WCL OAuth failed ({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        self._token = data["access_token"]
        self._token_expiry = time.monotonic() + max(0, int(data.get("expires_in", 0)) - 300)
        return self._token

    def graphql(self, query: str, variables: Optional[dict] = None) -> dict:
        # Retry once on 401: a cached token can be revoked before its assumed expiry.
        for attempt in range(2):
            token = self.ensure_token()
            resp = requests.post(
                API_URL,
                json={"query": query, "variables": variables or {}},
                headers={"Authorization": f"Bearer {token}"},
                timeout=TIMEOUT,
            )
            if resp.status_code == 401 and attempt == 0:
                self._token = None
                self._token_expiry = 0.0
                continue
            if resp.status_code != 200:
                raise WarcraftLogsError(f"WCL query failed ({resp.status_code}): {resp.text[:200]}")
            payload = resp.json()
            if payload.get("errors"):
                raise WarcraftLogsError(f"WCL GraphQL errors: {payload['errors']}")
            return payload
        raise WarcraftLogsError("WCL query failed after re-authenticating")

    def character_parses(self, name: str, server_slug: str, region: str, zone_id: int) -> ParseData:
        payload = self.graphql(
            ZONE_RANKINGS_QUERY,
            {"name": name, "server": server_slug, "region": region, "zone": int(zone_id)},
        )
        return _parse_zone_rankings(payload)

    def recent_report_codes(self, name: str, server_slug: str, region: str) -> List[ReportRef]:
        payload = self.graphql(
            RECENT_REPORTS_QUERY, {"name": name, "server": server_slug, "region": region}
        )
        return _parse_recent_reports(payload)

    def report_fights(self, code: str, difficulty: int) -> List[Fight]:
        return _parse_fights(self.graphql(FIGHTS_QUERY, {"code": code}), difficulty)

    def report_table(self, code: str, fight_ids: List[int], data_type: str) -> List[dict]:
        """data_type is a WCL TableDataType: Deaths, DamageTaken, Interrupts, Dispels."""
        payload = self.graphql(
            TABLE_QUERY, {"code": code, "fightIDs": list(fight_ids), "dataType": data_type}
        )
        return _parse_table_entries(payload)
