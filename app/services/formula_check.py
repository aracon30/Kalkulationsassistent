"""Plausibilitätsprüfung von Excel-Formel-Caches.

Ein kleiner, sicherer Auswerter (kein ``eval``) für einfache Formeln:
Grundrechenarten, Klammern, Prozent, Zellbezüge (auch blattübergreifend),
Bereiche sowie SUM/SUMME, ROUND/RUNDEN, MIN, MAX, ABS.

Ausgewertet wird jeweils nur eine Ebene: die gespeicherten Ergebniswerte der
referenzierten Zellen werden eingesetzt und mit dem gespeicherten Ergebnis der
Formelzelle verglichen. Damit lassen sich veraltete Caches erkennen (z. B. wenn
eine Datei von einem Programm gespeichert wurde, das nicht neu berechnet).
Nicht unterstützte Formeln ergeben ``None`` (nicht prüfbar) – nie einen Fehler.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable

from openpyxl.utils import column_index_from_string, get_column_letter

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<num>\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)
      | (?P<ref>(?:(?:'(?:[^']|'')+'|[A-Za-z0-9_.]+)!)?\$?[A-Za-z]{1,3}\$?\d+(?::\$?[A-Za-z]{1,3}\$?\d+)?)
      | (?P<func>[A-Za-z][A-Za-z0-9.]*)\s*\(
      | (?P<op>[-+*/(),;%^])
    )""",
    re.VERBOSE,
)


class _Unsupported(Exception):
    pass


@dataclass
class FormulaCheck:
    checkable: bool
    computed: float | None
    cached: float | None
    consistent: bool | None
    note: str = ""


Lookup = Callable[[str, str], tuple[Any, bool]]  # (sheet, address) -> (cached value, has_formula)


def _tokenize(text: str) -> list[tuple[str, str]]:
    pos, out = 0, []
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise _Unsupported(f"Unbekanntes Zeichen bei '{text[pos:pos+10]}'")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


class _Parser:
    def __init__(self, tokens: list[tuple[str, str]], sheet: str, lookup: Lookup):
        self.t, self.i, self.sheet, self.lookup = tokens, 0, sheet, lookup

    def peek(self) -> tuple[str, str] | None:
        return self.t[self.i] if self.i < len(self.t) else None

    def take(self) -> tuple[str, str]:
        tok = self.t[self.i]
        self.i += 1
        return tok

    def expect(self, value: str) -> None:
        tok = self.peek()
        if not tok or tok[1] != value:
            raise _Unsupported(f"'{value}' erwartet")
        self.i += 1

    def parse(self) -> float:
        v = self.expr()
        if self.peek() is not None:
            raise _Unsupported("Unerwartetes Ende")
        return v

    def expr(self) -> float:
        v = self.term()
        while (tok := self.peek()) and tok[1] in "+-":
            self.take()
            r = self.term()
            v = v + r if tok[1] == "+" else v - r
        return v

    def term(self) -> float:
        v = self.power()
        while (tok := self.peek()) and tok[1] in "*/":
            self.take()
            r = self.power()
            if tok[1] == "*":
                v *= r
            else:
                if r == 0:
                    raise _Unsupported("Division durch 0")
                v /= r
        return v

    def power(self) -> float:
        v = self.unary()
        while (tok := self.peek()) and tok[1] == "^":
            self.take()
            v = v ** self.unary()
        return v

    def unary(self) -> float:
        tok = self.peek()
        if tok and tok[1] in "+-":
            self.take()
            v = self.unary()
            return -v if tok[1] == "-" else v
        v = self.primary()
        while (tok := self.peek()) and tok[1] == "%":
            self.take()
            v /= 100.0
        return v

    def primary(self) -> float:
        if self.peek() is None:
            raise _Unsupported("Ausdruck unvollständig")
        kind, val = self.take()
        if kind == "num":
            return float(val)
        if kind == "ref":
            values = self.ref_values(val)
            if len(values) != 1:
                raise _Unsupported("Bereich außerhalb einer Funktion")
            return self.scalar(values[0])
        if kind == "func":
            return self.call(val.upper())
        if val == "(":
            v = self.expr()
            self.expect(")")
            return v
        raise _Unsupported(f"Unerwartetes Token {val}")

    def args(self) -> list[list[Any]]:
        out: list[list[Any]] = []
        if self.peek() and self.peek()[1] == ")":
            self.take()
            return out
        while True:
            tok = self.peek()
            nxt = self.t[self.i + 1] if self.i + 1 < len(self.t) else None
            if tok and tok[0] == "ref" and nxt and nxt[1] in {",", ";", ")"}:
                self.take()
                out.append(self.ref_values(tok[1]))
            else:
                out.append([self.expr()])
            tok = self.take() if self.peek() else None
            if not tok:
                raise _Unsupported("')' fehlt")
            if tok[1] == ")":
                return out
            if tok[1] not in {",", ";"}:
                raise _Unsupported("Trennzeichen erwartet")

    def call(self, name: str) -> float:
        args = self.args()
        flat = [v for group in args for v in group]
        nums = [float(v) for v in flat if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if name in {"SUM", "SUMME"}:
            return float(sum(nums))
        if name == "MIN":
            return float(min(nums)) if nums else 0.0
        if name == "MAX":
            return float(max(nums)) if nums else 0.0
        if name == "ABS" and len(args) == 1:
            return abs(self.scalar(args[0][0]))
        if name in {"ROUND", "RUNDEN"} and len(args) == 2:
            x, n = self.scalar(args[0][0]), int(self.scalar(args[1][0]))
            q = 10 ** n
            return math.floor(abs(x) * q + 0.5) / q * (1 if x >= 0 else -1)
        raise _Unsupported(f"Funktion {name} nicht unterstützt")

    def ref_values(self, ref: str) -> list[Any]:
        sheet = self.sheet
        if "!" in ref:
            sheet, ref = ref.rsplit("!", 1)
            if sheet.startswith("'"):
                sheet = sheet[1:-1].replace("''", "'")
        ref = ref.replace("$", "").upper()
        if ":" in ref:
            a, b = ref.split(":")
            ca, ra = _split(a)
            cb, rb = _split(b)
            if (abs(rb - ra) + 1) * (abs(cb - ca) + 1) > 5000:
                raise _Unsupported("Bereich zu groß")
            cells = [f"{get_column_letter(c)}{r}" for r in range(min(ra, rb), max(ra, rb) + 1) for c in range(min(ca, cb), max(ca, cb) + 1)]
        else:
            cells = [ref]
        out = []
        for addr in cells:
            value, has_formula = self.lookup(sheet, addr)
            if has_formula and value is None:
                raise _Unsupported(f"Referenzierte Formelzelle {sheet}!{addr} ohne gespeicherten Wert")
            out.append(value)
        return out

    @staticmethod
    def scalar(value: Any) -> float:
        if value is None:
            return 0.0
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, (int, float)):
            return float(value)
        raise _Unsupported("Textwert in Berechnung")


def _split(addr: str) -> tuple[int, int]:
    m = re.match(r"([A-Z]+)(\d+)$", addr)
    if not m:
        raise _Unsupported("Ungültiger Zellbezug")
    return column_index_from_string(m.group(1)), int(m.group(2))


def evaluate(formula: str, sheet: str, lookup: Lookup) -> float | None:
    text = formula[1:] if formula.startswith("=") else formula
    try:
        return _Parser(_tokenize(text), sheet, lookup).parse()
    except (_Unsupported, KeyError, ValueError, OverflowError, IndexError):
        return None


def check_formula(formula: str, cached: Any, sheet: str, lookup: Lookup, rel_tol: float = 1e-6, abs_tol: float = 0.01) -> FormulaCheck:
    cached_num = float(cached) if isinstance(cached, (int, float)) and not isinstance(cached, bool) else None
    computed = evaluate(formula, sheet, lookup)
    if computed is None:
        return FormulaCheck(False, None, cached_num, None, "Formel nicht automatisch nachrechenbar")
    if cached_num is None:
        return FormulaCheck(True, computed, None, None, "Kein gespeicherter Ergebniswert")
    ok = math.isclose(computed, cached_num, rel_tol=rel_tol, abs_tol=abs_tol)
    return FormulaCheck(True, computed, cached_num, ok, "" if ok else "Gespeicherter Wert weicht von Nachrechnung ab")
