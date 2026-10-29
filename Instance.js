.pragma library

function moduleId(manifest) {
  return manifest && typeof manifest.id === "string" && manifest.id ? manifest.id : "urbit.dms"
}
function suffix(id) {
  return id.indexOf("urbit.dms.") === 0 ? " (" + id.slice(10) + ")" : ""
}
function label(id) { return "Urbit DMs" + suffix(id) }
function layerNamespace(id) { return id.replace(/\./g, "-") }
