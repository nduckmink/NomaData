// NomaData — Cube configuration.
//
// M0: Cube boots but defines no models yet. In M2/M3 NomaData will generate
// model files under ./model/ from the *published* semantic graph, and route
// analytical queries through Cube.
//
// Architectural note: Cube is an implementation layer. NomaData must not expose
// Cube-specific concepts through the application — the app speaks AnalyticalQuery
// (see apps/api/nomadata/core/models.py), the query engine adapter speaks Cube.
//
// The database connection comes from CUBEJS_DB_* environment variables
// (see docker-compose.yml / cube/.env.example).

module.exports = {};
