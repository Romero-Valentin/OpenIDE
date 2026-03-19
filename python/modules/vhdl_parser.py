"""Minimal VHDL parser — extracts entity name and port list from a .vhd file."""

import re


def parse_vhdl_file(filepath: str) -> dict | None:
    """Parse a VHDL file and return entity info.

    Returns a dict with keys 'entity', 'library', 'ports' or None on failure.
    Each port is {'name': str, 'direction': str, 'side': str}.
    """
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # Strip single-line comments
    content_no_comments = re.sub(r"--[^\n]*", "", content)

    # Find entity declaration
    entity_match = re.search(
        r"\bentity\s+(\w+)\s+is\b", content_no_comments, re.IGNORECASE
    )
    if not entity_match:
        return None
    entity_name = entity_match.group(1)

    # Extract the port(...) block, handling nested parentheses in types
    port_block = _extract_port_block(content_no_comments)
    if port_block is None:
        return {"entity": entity_name, "library": "work", "ports": []}

    ports = _parse_port_block(port_block)
    return {"entity": entity_name, "library": "work", "ports": ports}


def _extract_port_block(content: str) -> str | None:
    """Extract the content inside port(...) handling nested parentheses."""
    match = re.search(r"\bport\s*\(", content, re.IGNORECASE)
    if not match:
        return None
    start = match.end()
    depth = 1
    i = start
    while i < len(content) and depth > 0:
        if content[i] == "(":
            depth += 1
        elif content[i] == ")":
            depth -= 1
        i += 1
    if depth != 0:
        return None
    return content[start : i - 1]


def _parse_port_block(port_block: str) -> list[dict]:
    """Parse the inside of a port(...) block into a list of port dicts."""
    ports = []
    # Split on semicolons to get individual port declarations
    declarations = [d.strip() for d in port_block.split(";") if d.strip()]

    for decl in declarations:
        # Pattern: name1, name2 : direction type
        match = re.match(
            r"([\w\s,]+)\s*:\s*(in|out|inout|buffer)\b",
            decl.strip(),
            re.IGNORECASE,
        )
        if not match:
            continue
        names_str = match.group(1)
        direction = match.group(2).lower()
        side = "left" if direction == "in" else "right"

        for name in names_str.split(","):
            name = name.strip()
            if name:
                ports.append({"name": name, "direction": direction, "side": side})
    return ports
