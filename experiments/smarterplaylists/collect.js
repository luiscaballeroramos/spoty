globalThis.smarterPlaylistsLab = (() => {
  const prefix = 'LAB metrics 2026-10-15';
  const supported = {
    artist: ['artist_area', 'artist_country', 'artist_end_year', 'artist_gender',
      'artist_start_year', 'artist_type', 'followers', 'name', 'popularity', 'uri'],
    album: ['album_type', 'artist', 'label', 'name', 'popularity', 'release_date',
      'total_tracks', 'uri'],
  };
  const sourceTypes = {track: 'TrackSource', artist: 'ArtistURIs', album: 'AlbumURIs'};

  async function request(path, body, method = 'GET') {
    const response = await fetch(path, {
      method,
      ...(body === undefined ? {} : {
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
      }),
    });
    if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
    return response.json();
  }

  async function start(targets) {
    if (location.origin !== 'https://smarterplaylists.playlistmachinery.com') {
      throw new Error('Run only in the authorized Smarter Playlists tab.');
    }
    await request('/api/auth/me');
    const inventory = await request('/api/inventory');
    const programs = await request('/api/programs/');
    const sortable = inventory.find(item => item.name === 'Sorter').params.properties.attr.enum;
    const manifest = {started_at: new Date().toISOString(), runs: {}};
    for (const entity of ['track', 'artist', 'album']) {
      if (!targets[entity]?.uri?.startsWith(`spotify:${entity}:`)) {
        throw new Error(`Invalid ${entity} target.`);
      }
    }
    for (const entity of ['track', 'artist', 'album']) {
      const name = `${prefix} ${entity}`;
      const attrs = entity === 'track'
        ? sortable.filter(attr => !['random', 'source', 'source_count'].includes(attr))
        : supported[entity];
      const components = {
        source: {type: sourceTypes[entity], params: {
          uris: targets[entity].uri, name: targets[entity].name, cache_mode: 'auto',
        }, sources: {}},
      };
      const branches = ['source'];
      for (const attr of attrs) {
        const node = `sort_${attr}`;
        components[node] = {type: 'Sorter', params: {attr, reverse: false},
          sources: {green: ['source']}};
        branches.push(node);
      }
      if (entity === 'track') {
        components.genres = {type: 'GenreFilter', params: {
          genre: '', match_type: 'substring', primary_genre_only: false, invert: false,
        }, sources: {green: ['source']}};
        branches.push('genres');
      }
      components.results = {type: 'Concatenate', params: {}, sources: {green: branches}};
      const body = {name, program_json: {name, main: 'results', components}};
      const existing = programs.find(program => program.name === name);
      const saved = existing
        ? await request(`/api/programs/${existing.pid}`, body, 'PUT')
        : await request('/api/programs/', body, 'POST');
      const pid = existing?.pid ?? saved.pid;
      const run = await request(`/api/programs/${pid}/run?save=false`, undefined, 'POST');
      manifest.runs[entity] = {pid, run_id: run.run_id, requested_attributes: attrs};
    }
    return manifest;
  }

  async function collect(manifest) {
    const report = {captured_at: new Date().toISOString(), manifest, entities: {}};
    for (const [entity, run] of Object.entries(manifest.runs)) {
      const raw = await request(`/api/runs/${run.run_id}/status`);
      if (raw.status !== 'complete') throw new Error(`${entity}: ${raw.status}`);
      if (raw.playlist_uri) throw new Error('Unexpected Spotify playlist output.');
      const values = {};
      const conflicts = {};
      for (const row of raw.tracks ?? []) {
        for (const [field, value] of Object.entries(row)) {
          if (value === null || value === undefined) continue;
          if (field in values && JSON.stringify(values[field]) !== JSON.stringify(value)) {
            (conflicts[field] ??= [values[field]]).push(value);
          } else {
            values[field] = value;
          }
        }
      }
      report.entities[entity] = {
        values, conflicts,
        missing_requested: run.requested_attributes.filter(field => !(field in values)),
        raw,
      };
    }
    return report;
  }

  return {start, collect};
})();
