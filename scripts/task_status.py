"""Recount checkboxes per phase in task.md and refresh the Live status table."""
import io
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "task.md"
text = io.open(path, encoding="utf-8").read()

# Split body into phases by "## P<n>:" headings.
counts = {}
current = None
for line in text.splitlines():
    m = re.match(r"^## (P\d+):", line)
    if m:
        current = m.group(1)
        counts[current] = [0, 0]
        continue
    if line.startswith("## "):
        current = None
        continue
    if current and re.match(r"^\s*- \[( |x)\]", line):
        counts[current][1] += 1
        if re.match(r"^\s*- \[x\]", line):
            counts[current][0] += 1

def status(done, total):
    if total and done == total:
        return "✅"
    if done:
        return "🟨"
    return "⬜"

out = []
tot_d = tot_t = 0
for line in text.splitlines():
    m = re.match(r"^\| (P\d+) ([^|]+)\| ([^|]+)\| [^|]+\| [^|]+\|$", line)
    if m and m.group(1) in counts:
        d, t = counts[m.group(1)]
        tot_d += d
        tot_t += t
        line = f"| {m.group(1)} {m.group(2)}| {m.group(3)}| {status(d, t)} | {d} / {t} |"
    elif line.startswith("| **Total** |"):
        line = f"| **Total** | | | **{tot_d} / {tot_t}** |"
    out.append(line)

io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(out) + "\n")  # keep LF on Windows
for k, (d, t) in counts.items():
    print(k, d, "/", t)
print("TOTAL", tot_d, "/", tot_t)
