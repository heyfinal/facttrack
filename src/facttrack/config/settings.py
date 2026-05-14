"""FactTrack runtime configuration.

All knobs live here — no hardcoded paths or secrets in module code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CountyConfig:
    fips: str
    name: str
    state: str
    opr_platform: str
    opr_base_url: str | None
    notes: str = ""


COUNTIES: dict[str, CountyConfig] = {
    "48001": CountyConfig(fips="48001", name="Anderson",    state="TX",
        opr_platform="publicsearch", opr_base_url="https://anderson.tx.publicsearch.us",
        notes="Palestine seat. Primary demo county. Smith Co (Tyler) is adjacent."),
    "48073": CountyConfig(fips="48073", name="Cherokee",    state="TX",
        opr_platform="manual", opr_base_url=None,
        notes="Rusk seat. NOT on publicsearch.us — courthouse-only OPR. RRC scope only."),
    "48161": CountyConfig(fips="48161", name="Freestone",   state="TX",
        opr_platform="publicsearch", opr_base_url="https://freestone.tx.publicsearch.us",
        notes="Fairfield seat. Southwest of Anderson."),
    "48213": CountyConfig(fips="48213", name="Henderson",   state="TX",
        opr_platform="manual", opr_base_url=None,
        notes="Athens seat. NOT on publicsearch.us. RRC scope only."),
    "48225": CountyConfig(fips="48225", name="Houston",     state="TX",
        opr_platform="idocket_paid", opr_base_url=None,
        notes="Crockett seat. OPR behind paid iDocket subscription; RRC scope only here."),
    "48289": CountyConfig(fips="48289", name="Leon",        state="TX",
        opr_platform="publicsearch", opr_base_url="https://leon.tx.publicsearch.us",
        notes="Centerville seat. South of Anderson."),
    "48313": CountyConfig(fips="48313", name="Madison",     state="TX",
        opr_platform="publicsearch", opr_base_url="https://madison.tx.publicsearch.us",
        notes="Madisonville seat. South of Houston Co."),
    "48347": CountyConfig(fips="48347", name="Nacogdoches", state="TX",
        opr_platform="publicsearch", opr_base_url="https://nacogdoches.tx.publicsearch.us",
        notes="Nacogdoches seat. East of Houston Co."),
    "48423": CountyConfig(fips="48423", name="Smith",       state="TX",
        opr_platform="publicsearch", opr_base_url="https://smith.tx.publicsearch.us",
        notes="Tyler seat — Monument HQ. High-priority scan target."),
    "48455": CountyConfig(fips="48455", name="Trinity",     state="TX",
        opr_platform="manual", opr_base_url=None,
        notes="Groveton seat. NOT on publicsearch.us. RRC scope only."),
    "48471": CountyConfig(fips="48471", name="Walker",      state="TX",
        opr_platform="publicsearch", opr_base_url="https://walker.tx.publicsearch.us",
        notes="Huntsville seat. Free Odyssey probate docket available (only one of 7)."),
}


@dataclass(frozen=True)
class DBConfig:
    host: str = field(default_factory=lambda: os.environ.get("FT_DB_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("FT_DB_PORT", "5432")))
    user: str = field(default_factory=lambda: os.environ.get("FT_DB_USER", "daniel"))
    password: str = field(default_factory=lambda: os.environ.get("FT_DB_PASSWORD", ""))
    database: str = field(default_factory=lambda: os.environ.get("FT_DB_NAME", "facttrack"))

    @property
    def dsn(self) -> str:
        pw = f":{self.password}" if self.password else ""
        return f"postgresql://{self.user}{pw}@{self.host}:{self.port}/{self.database}"


@dataclass(frozen=True)
class HTTPConfig:
    user_agent: str = "FactTrack/0.1 (+research; +daniel@example.com)"
    rrc_rate_limit_per_sec: float = 1.0
    county_rate_limit_per_sec: float = 0.5
    request_timeout_sec: float = 20.0
    max_retries: int = 3


@dataclass(frozen=True)
class LLMConfig:
    ollama_host: str = field(
        default_factory=lambda: os.environ.get("FT_OLLAMA_HOST", "http://192.168.1.240:11434")
    )
    ollama_model: str = field(
        default_factory=lambda: os.environ.get("FT_OLLAMA_MODEL", "qwen2.5:7b-instruct")
    )
    openrouter_api_key: str = field(
        default_factory=lambda: os.environ.get("OPENROUTER_API_KEY", "")
    )
    openrouter_model: str = field(
        default_factory=lambda: os.environ.get("FT_OR_MODEL", "deepseek/deepseek-r1")
    )
    use_openrouter_fallback: bool = field(
        default_factory=lambda: os.environ.get("FT_OR_FALLBACK", "0") == "1"
    )


@dataclass(frozen=True)
class Paths:
    root: Path = field(default_factory=lambda: Path(os.environ.get("FT_ROOT", str(Path.home() / "facttrack"))))

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def reports(self) -> Path:
        return self.root / "reports"

    @property
    def cache(self) -> Path:
        return self.root / "cache"


DB = DBConfig()
HTTP = HTTPConfig()
LLM = LLMConfig()
PATHS = Paths()


def ensure_dirs() -> None:
    for p in (PATHS.data, PATHS.runs, PATHS.reports, PATHS.cache):
        p.mkdir(parents=True, exist_ok=True)
