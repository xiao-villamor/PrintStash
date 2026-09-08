def count($severity):
  [.matches[]? | select(.vulnerability.severity == $severity)] | length;

def fixable:
  [.matches[]? | select((.vulnerability.fix.versions // []) | length > 0)] | length;

"## Grype container vulnerability report\n\n" +
"**Image:** `\($image)`  \n" +
"**Matches:** \(.matches | length) (\(fixable) with a known fix)\n\n" +
"| Severity | Matches |\n|---|---:|\n" +
"| Critical | \(count("Critical")) |\n" +
"| High | \(count("High")) |\n" +
"| Medium | \(count("Medium")) |\n" +
"| Low | \(count("Low")) |\n" +
"| Negligible | \(count("Negligible")) |\n" +
"| Unknown | \(count("Unknown")) |"
