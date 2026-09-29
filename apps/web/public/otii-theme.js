// Apply the Otii Learn theme before the first paint (see otii/theme.ts).
(function () {
  var match = document.cookie.match(/(?:^|; )otii_learn_theme=([a-z0-9-]+)/)
  if (match) document.documentElement.setAttribute('data-otii-theme', match[1])
})()
