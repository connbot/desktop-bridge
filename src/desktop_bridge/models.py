from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DesktopAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["click", "move", "scroll", "drag", "type", "key"]
    x: int = Field(default=0, ge=0, lt=1280)
    y: int = Field(default=0, ge=0, lt=800)
    button: Literal["left", "right", "middle"] = "left"
    count: int = Field(default=1, ge=1, le=2)
    dx: int = Field(default=0, ge=-20, le=20)
    dy: int = Field(default=0, ge=-20, le=20)
    path: list[list[int]] = Field(default_factory=list, max_length=100)
    text: str = Field(default="", max_length=20000)
    keys: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def validate_action(self):
        if self.kind == "drag":
            if len(self.path) < 2 or any(
                len(p) != 2 or not (0 <= p[0] < 1280 and 0 <= p[1] < 800) for p in self.path
            ):
                raise ValueError("Drag requires 2–100 in-bounds [x,y] points")
        if self.kind == "key":
            import re

            if not self.keys or any(not re.fullmatch(r"[A-Za-z0-9_]{1,24}", k) for k in self.keys):
                raise ValueError("Use canonical key names: ctrl, shift, alt, enter, a, F1, etc.")
        return self


class BrowserAction(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: Literal["navigate", "click", "fill", "press"]
    url: str = Field(default="", max_length=4000)
    role: str = Field(default="", max_length=80)
    name: str = Field(default="", max_length=1000)
    text: str = Field(default="", max_length=20000)
    key: str = Field(default="Enter", max_length=80)

    @model_validator(mode="after")
    def validate_action(self):
        if self.kind == "navigate":
            u = urlsplit(self.url)
            if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password:
                raise ValueError("Use an HTTP(S) URL without embedded credentials")
        elif not self.role:
            raise ValueError("Provide an exact accessible role and name")
        return self
