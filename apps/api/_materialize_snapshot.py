import json, re, pathlib

src = pathlib.Path("/home/joe/.claude/projects/-mnt-c-Users-Joe-2ndActCapital/a26164d9-7fd1-4b1c-b5dd-1321ab61afe2/tool-results/mcp-supabase-2ndact-dev-execute_sql-1785339157887.txt")
raw = src.read_text()

# Extract the JSON array (first '[' to matching last ']')
start = raw.index("[")
end = raw.rindex("]") + 1
rows = json.loads(raw[start:end])
content = rows[0]["file"]

out = pathlib.Path("/mnt/c/Users/Joe/2ndActCapital/docs/schema_snapshot.sql.new")
out.write_text(content)
print("wrote", out, "chars:", len(content), "lines:", content.count(chr(10)))
