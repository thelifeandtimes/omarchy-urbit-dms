function normal(text) {
  var value = String(text || "").toLowerCase().trim()
  try { value = value.normalize("NFKD").replace(/[\u0300-\u036f]/g, "") } catch (e) { }
  return value.replace(/^~/, "")
}

function distance(a, b, limit) {
  if (Math.abs(a.length - b.length) > limit) return limit + 1
  var row = []
  for (var j = 0; j <= b.length; j++) row[j] = j
  for (var i = 1; i <= a.length; i++) {
    var next = [i]
    for (j = 1; j <= b.length; j++)
      next[j] = Math.min(next[j - 1] + 1, row[j] + 1, row[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1))
    row = next
  }
  return row[b.length]
}

function score(query, value) {
  var term = normal(query)
  var candidate = normal(value)
  if (!term || !candidate) return 0
  if (term === candidate) return 1000
  if (candidate.indexOf(term) === 0) return 800 - Math.min(100, candidate.length - term.length)
  var pos = candidate.indexOf(term)
  if (pos >= 0) return 600 - Math.min(100, pos)
  var at = 0, gaps = 0, previous = -1
  for (var i = 0; i < candidate.length && at < term.length; i++) {
    if (candidate[i] === term[at]) {
      if (previous >= 0) gaps += i - previous - 1
      previous = i
      at++
    }
  }
  if (at === term.length) return 350 - Math.min(100, gaps)
  // Typo tolerance for a nearly complete nickname/@p, not a broad query.
  if (term.length >= 4 && Math.abs(term.length - candidate.length) <= 2) {
    var edits = distance(term, candidate, 2)
    if (edits <= 2) return 220 - edits * 40
  }
  return 0
}

function find(query, rows, contacts) {
  var term = normal(query)
  if (!term) return (rows || []).slice()
  var seen = {}, all = []
  for (var i = 0; i < (rows || []).length; i++) {
    var row = rows[i]
    if (!row || !row.ship) continue
    seen[row.ship] = true
    all.push(row)
  }
  for (var id in (contacts || {})) {
    if (!id.startsWith("~") || seen[id]) continue
    all.push({ship: id, name: contacts[id] || id, preview: "", recency: 0})
  }
  var matches = []
  for (i = 0; i < all.length; i++) {
    var hit = all[i]
    var strength = Math.max(score(term, hit.ship), score(term, hit.name))
    if (strength > 0) matches.push({row: hit, strength: strength})
  }
  matches.sort(function(a, b) {
    return b.strength - a.strength || (b.row.recency || 0) - (a.row.recency || 0) || a.row.ship.localeCompare(b.row.ship)
  })
  return matches.map(function(hit) { return hit.row })
}

function recipients(query, rows, contacts) {
  var term = String(query || "").trim().toLowerCase()
  if (!term) return []
  var found = find(term, rows, contacts)
  // Only offer a literal unlisted target when the input resembles a complete
  // Urbit ID (not while someone is still typing a prefix). Keep it last, and
  // never silently select it ahead of a known contact.
  var literal = /^~[a-z]{3}(?:[a-z]{3})?(?:-[a-z]{6})*$/.test(term) &&
    !found.some(function(row) { return row.ship === term })
  var matches = found.slice(0, literal ? 5 : 6)
  if (literal) matches.push({ship: term, name: term, recency: 0, literal: true})
  return matches
}
