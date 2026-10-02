function compactDate(value, nowValue) {
  var stamp = Number(value)
  if (!isFinite(stamp) || stamp <= 0) return "No messages yet"
  var date = new Date(stamp)
  var now = nowValue === undefined ? new Date() : new Date(nowValue)
  if (isNaN(date.getTime()) || isNaN(now.getTime())) return "No messages yet"
  var day = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  var today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  var days = Math.round((today.getTime() - day.getTime()) / 86400000)
  var label
  if (days === 0) label = "Today"
  else if (days === 1) label = "Yesterday"
  else if (days >= 2 && days < 7) label = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"][date.getDay()]
  else if (days >= 7 && days < 14) label = "Last Week"
  else label = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"][date.getMonth()] + " " + date.getDate()
  var hour = date.getHours() % 12 || 12
  var minute = ("0" + date.getMinutes()).slice(-2)
  return label + " " + hour + ":" + minute + " " + (date.getHours() < 12 ? "AM" : "PM")
}
