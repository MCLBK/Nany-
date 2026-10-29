/* ============================================================
   NANYÉ — Configuration du frontend
   - En local (localhost / 127.0.0.1) : API lancée à part sur le port 8000.
   - En ligne : l'API est servie par le même domaine, sous /api
     (un seul projet Vercel pour le site et l'API : pas de CORS entre domaines,
     cookie de session « same-site »).
   Pour une API hébergée ailleurs, remplacer simplement la valeur de production.
   ============================================================ */
(function () {
  var local = /^(localhost|127\.0\.0\.1)$/.test(location.hostname);
  window.NANYE_CONFIG = {
    apiBaseUrl: local ? 'http://localhost:8000/api' : '/api'
  };
})();
