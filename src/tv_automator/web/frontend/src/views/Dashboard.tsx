import React, { useEffect, useMemo, useState } from 'react';
import { useTvAutomator, type Game } from '../hooks/useTvAutomator';
import { Play, Tv, ChevronLeft, ChevronRight, AlertTriangle, MapPin, Activity } from 'lucide-react';
import './Dashboard.css';

// ── Helpers ──────────────────────────────────────────────────

const toIsoDate = (d: Date) => d.toISOString().slice(0, 10);
const fmtDayLabel = (d: Date) => {
  const today = toIsoDate(new Date());
  const target = toIsoDate(d);
  if (today === target) return 'Today';
  const diff = Math.round((d.getTime() - new Date().setHours(0,0,0,0)) / (1000*60*60*24));
  if (diff === -1) return 'Yesterday';
  if (diff === 1) return 'Tomorrow';
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
};

type Filter = 'all' | 'live' | 'upcoming' | 'final';

// ── Compact list item ────────────────────────────────────────

const GameListItem: React.FC<{
  game: Game;
  isSelected: boolean;
  isPlaying: boolean;
  onClick: () => void;
}> = ({ game, isSelected, isPlaying, onClick }) => {
  const isLive = game.status === 'live';
  const hasScore = game.display_score && game.display_score.trim() !== '';

  return (
    <div
      className={`gli ${isLive ? 'gli--live' : ''} ${isSelected ? 'gli--selected' : ''} ${isPlaying ? 'gli--playing' : ''}`}
      onClick={onClick}
      role="button"
      tabIndex={0}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick(); } }}
    >
      <div className="gli-left">
        <div className="gli-teams">
          <span className="gli-team">{game.away_team.abbreviation}</span>
          <span className="gli-at">@</span>
          <span className="gli-team">{game.home_team.abbreviation}</span>
        </div>
        <div className="gli-meta">
          {isLive && <span className="live-pulse-dot" />}
          <span className={`gli-status gli-status--${game.status}`}>{game.status_label}</span>
          {!isLive && !hasScore && <span className="gli-time">· {game.display_time}</span>}
        </div>
      </div>
      {hasScore && (
        <div className="gli-score">
          <span>{game.away_team.score ?? 0}</span>
          <span>{game.home_team.score ?? 0}</span>
        </div>
      )}
    </div>
  );
};

// ── Detail panel ─────────────────────────────────────────────

interface GameStats {
  info: {
    away_name: string; away_abbr: string;
    home_name: string; home_abbr: string;
    venue: string; date: string; status: string;
  };
  linescore: {
    innings: { num: number|string; away_r:any; away_h:any; away_e:any; home_r:any; home_h:any; home_e:any; }[];
    away: { runs:number; hits:number; errors:number; leftOnBase:number };
    home: { runs:number; hits:number; errors:number; leftOnBase:number };
  };
  scoring_plays: { inning:any; half:string; desc:string; away:number; home:number }[];
  away_pitchers: any[];
  home_pitchers: any[];
  away_batting: any;
  home_batting: any;
}

const GameDetailPanel: React.FC<{
  game: Game;
  isPlaying: boolean;
  onPlay: (gameId: string, feed: string) => void;
}> = ({ game, isPlaying, onPlay }) => {
  const [stats, setStats] = useState<GameStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setStats(null);
    setError(null);
    setLoading(true);

    const load = async () => {
      try {
        const r = await fetch(`/api/game/${game.game_id}/stats`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        if (!cancelled) setStats(data);
      } catch (e: any) {
        if (!cancelled) setError(e.message || 'Failed to load stats');
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    load();

    // Keep the detail panel fresh for live games.
    const iv = game.status === 'live' ? setInterval(load, 20000) : null;
    return () => {
      cancelled = true;
      if (iv) clearInterval(iv);
    };
  }, [game.game_id, game.status]);

  const isLive = game.status === 'live';
  const maxInnings = stats ? Math.max(9, stats.linescore.innings.length) : 9;
  const inningNums = Array.from({ length: maxInnings }, (_, i) => i + 1);

  return (
    <div className="detail-panel glass-panel animate-in" key={game.game_id}>
      {/* Header */}
      <div className="detail-head">
        <div className="detail-head-teams">
          <div className="detail-team">
            <div className="detail-team-abbr">{game.away_team.abbreviation}</div>
            <div className="detail-team-name">{game.away_team.name}</div>
            <div className="detail-team-score">{game.away_team.score ?? '—'}</div>
          </div>
          <div className="detail-vs">@</div>
          <div className="detail-team">
            <div className="detail-team-abbr">{game.home_team.abbreviation}</div>
            <div className="detail-team-name">{game.home_team.name}</div>
            <div className="detail-team-score">{game.home_team.score ?? '—'}</div>
          </div>
        </div>

        <div className="detail-meta">
          <span className={`detail-status detail-status--${game.status}`}>
            {isLive && <span className="live-pulse-dot" />}
            {game.status_label}
            {isLive && stats?.info.status && ` · ${stats.info.status}`}
          </span>
          <span className="detail-meta-item"><MapPin size={14} /> {game.venue || stats?.info.venue || '—'}</span>
          <span className="detail-meta-item">{game.display_time}</span>
        </div>

        {/* Play buttons */}
        {game.is_watchable && (
          <div className="detail-play-row">
            <button
              className="btn btn-neon"
              onClick={() => onPlay(game.game_id, 'HOME')}
              disabled={isPlaying}
            >
              <Play size={14} /> {isPlaying ? 'Now Playing' : 'Home Feed'}
            </button>
            <button className="btn" onClick={() => onPlay(game.game_id, 'AWAY')} disabled={isPlaying}>
              Away Feed
            </button>
          </div>
        )}
      </div>

      {/* Probable pitchers (pre-game) */}
      {(game.extra?.away_probable_pitcher || game.extra?.home_probable_pitcher) && !isLive && (
        <section className="detail-section">
          <h3 className="detail-section-title">Probable Pitchers</h3>
          <div className="pp-row">
            <div><span className="muted">{game.away_team.abbreviation}</span> {game.extra.away_probable_pitcher || 'TBD'}</div>
            <div><span className="muted">{game.home_team.abbreviation}</span> {game.extra.home_probable_pitcher || 'TBD'}</div>
          </div>
        </section>
      )}

      {loading && !stats && <div className="detail-loading">Loading stats…</div>}
      {error && <div className="detail-error">Could not load stats: {error}</div>}

      {stats && (
        <>
          {/* Linescore */}
          <section className="detail-section">
            <h3 className="detail-section-title"><Activity size={14} /> Linescore</h3>
            <div className="linescore-wrap">
              <table className="linescore">
                <thead>
                  <tr>
                    <th className="ls-team"></th>
                    {inningNums.map(n => <th key={n}>{n}</th>)}
                    <th className="ls-total">R</th>
                    <th className="ls-total">H</th>
                    <th className="ls-total">E</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="ls-team">{stats.info.away_abbr || game.away_team.abbreviation}</td>
                    {inningNums.map(n => {
                      const inn = stats.linescore.innings.find(i => Number(i.num) === n);
                      return <td key={n} className="ls-inning">{inn?.away_r ?? ''}</td>;
                    })}
                    <td className="ls-total">{stats.linescore.away.runs}</td>
                    <td className="ls-total">{stats.linescore.away.hits}</td>
                    <td className="ls-total">{stats.linescore.away.errors}</td>
                  </tr>
                  <tr>
                    <td className="ls-team">{stats.info.home_abbr || game.home_team.abbreviation}</td>
                    {inningNums.map(n => {
                      const inn = stats.linescore.innings.find(i => Number(i.num) === n);
                      return <td key={n} className="ls-inning">{inn?.home_r ?? ''}</td>;
                    })}
                    <td className="ls-total">{stats.linescore.home.runs}</td>
                    <td className="ls-total">{stats.linescore.home.hits}</td>
                    <td className="ls-total">{stats.linescore.home.errors}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </section>

          {/* Scoring plays */}
          {stats.scoring_plays.length > 0 && (
            <section className="detail-section">
              <h3 className="detail-section-title">Scoring Plays</h3>
              <ul className="scoring-list">
                {stats.scoring_plays.map((p, i) => (
                  <li key={i} className="scoring-item">
                    <span className="scoring-inning">
                      {p.half?.toLowerCase() === 'top' ? 'T' : 'B'}{p.inning}
                    </span>
                    <span className="scoring-desc">{p.desc}</span>
                    <span className="scoring-score">{p.away}–{p.home}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* Pitchers */}
          {(stats.away_pitchers.length > 0 || stats.home_pitchers.length > 0) && (
            <section className="detail-section">
              <h3 className="detail-section-title">Pitchers</h3>
              <div className="pitcher-grid">
                <PitcherTable team={stats.info.away_abbr || game.away_team.abbreviation} pitchers={stats.away_pitchers} />
                <PitcherTable team={stats.info.home_abbr || game.home_team.abbreviation} pitchers={stats.home_pitchers} />
              </div>
            </section>
          )}

          {/* Batting totals */}
          <section className="detail-section">
            <h3 className="detail-section-title">Batting Totals</h3>
            <div className="batting-totals">
              <BattingRow team={stats.info.away_abbr || game.away_team.abbreviation} s={stats.away_batting} />
              <BattingRow team={stats.info.home_abbr || game.home_team.abbreviation} s={stats.home_batting} />
            </div>
          </section>
        </>
      )}
    </div>
  );
};

const PitcherTable: React.FC<{ team: string; pitchers: any[] }> = ({ team, pitchers }) => (
  <div>
    <div className="pitcher-team-head">{team}</div>
    <table className="pitcher-table">
      <thead>
        <tr>
          <th>Name</th><th>IP</th><th>H</th><th>R</th><th>ER</th><th>BB</th><th>K</th><th>ERA</th>
        </tr>
      </thead>
      <tbody>
        {pitchers.length === 0 && <tr><td colSpan={8} className="muted">No data</td></tr>}
        {pitchers.map((p: any, i: number) => (
          <tr key={i}>
            <td className="pitcher-name">
              {p.name || p.fullName || '—'}
              {p.note && <span className="pitcher-note"> {p.note}</span>}
            </td>
            <td>{p.ip ?? p.inningsPitched ?? '—'}</td>
            <td>{p.h ?? p.hits ?? '—'}</td>
            <td>{p.r ?? p.runs ?? '—'}</td>
            <td>{p.er ?? p.earnedRuns ?? '—'}</td>
            <td>{p.bb ?? p.baseOnBalls ?? '—'}</td>
            <td>{p.k ?? p.strikeOuts ?? '—'}</td>
            <td>{p.era ?? '—'}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

const BattingRow: React.FC<{ team: string; s: any }> = ({ team, s }) => (
  <div className="batting-row">
    <span className="batting-team">{team}</span>
    <span><span className="muted">AB</span> {s?.atBats ?? 0}</span>
    <span><span className="muted">R</span> {s?.runs ?? 0}</span>
    <span><span className="muted">H</span> {s?.hits ?? 0}</span>
    <span><span className="muted">HR</span> {s?.homeRuns ?? 0}</span>
    <span><span className="muted">BB</span> {s?.baseOnBalls ?? 0}</span>
    <span><span className="muted">K</span> {s?.strikeOuts ?? 0}</span>
    <span><span className="muted">LOB</span> {s?.leftOnBase ?? 0}</span>
  </div>
);

// ── Main Dashboard ───────────────────────────────────────────

const Dashboard: React.FC = () => {
  const { games: todayGames, status, playGame, refreshGames } = useTvAutomator();

  const [date, setDate] = useState<Date>(() => {
    const d = new Date();
    d.setHours(0,0,0,0);
    return d;
  });
  const [filter, setFilter] = useState<Filter>('all');
  const [dateGames, setDateGames] = useState<Game[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const isToday = toIsoDate(date) === toIsoDate(new Date());

  // Fetch games for non-today dates; otherwise use the context cache.
  useEffect(() => {
    if (isToday) { setDateGames(null); return; }
    let cancelled = false;
    setLoading(true);
    fetch(`/api/games?date=${toIsoDate(date)}`)
      .then(r => r.json())
      .then(data => { if (!cancelled) setDateGames(Array.isArray(data) ? data : []); })
      .catch(() => { if (!cancelled) setDateGames([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [date, isToday]);

  const games = isToday ? todayGames : (dateGames ?? []);

  const filteredGames = useMemo(() => {
    let list = games;
    if (filter === 'live')     list = games.filter(g => g.status === 'live');
    if (filter === 'upcoming') list = games.filter(g => g.status === 'scheduled' || g.status === 'pre_game');
    if (filter === 'final')    list = games.filter(g => g.status === 'final');
    return list;
  }, [games, filter]);

  // Auto-select: prefer currently playing > first live > first game
  useEffect(() => {
    if (selectedId && games.some(g => g.game_id === selectedId)) return; // keep current selection if still present
    const preferred =
      games.find(g => g.game_id === status.now_playing_game_id) ||
      games.find(g => g.status === 'live') ||
      games[0];
    setSelectedId(preferred?.game_id ?? null);
  }, [games, status.now_playing_game_id]); // eslint-disable-line react-hooks/exhaustive-deps

  const selectedGame = games.find(g => g.game_id === selectedId) || null;

  const counts = {
    live: games.filter(g => g.status === 'live').length,
    upcoming: games.filter(g => g.status === 'scheduled' || g.status === 'pre_game').length,
    final: games.filter(g => g.status === 'final').length,
  };

  const shiftDate = (days: number) => {
    const d = new Date(date);
    d.setDate(d.getDate() + days);
    setDate(d);
  };

  return (
    <div className="dash animate-in">
      {/* Top bar — date + filters */}
      <div className="dash-topbar">
        <div className="dash-date-nav">
          <button className="btn-icon" onClick={() => shiftDate(-1)} title="Previous day"><ChevronLeft size={18} /></button>
          <div className="dash-date-label">{fmtDayLabel(date)}</div>
          <button className="btn-icon" onClick={() => shiftDate(1)} title="Next day"><ChevronRight size={18} /></button>
          {!isToday && (
            <button className="btn" onClick={() => { const d = new Date(); d.setHours(0,0,0,0); setDate(d); }}>
              Jump to Today
            </button>
          )}
        </div>

        <div className="dash-filters">
          <button className={`filter-pill ${filter==='all'?'active':''}`} onClick={() => setFilter('all')}>
            All <span className="pill-count">{games.length}</span>
          </button>
          <button className={`filter-pill ${filter==='live'?'active':''}`} onClick={() => setFilter('live')}>
            <span className="live-pulse-dot" /> Live <span className="pill-count">{counts.live}</span>
          </button>
          <button className={`filter-pill ${filter==='upcoming'?'active':''}`} onClick={() => setFilter('upcoming')}>
            Upcoming <span className="pill-count">{counts.upcoming}</span>
          </button>
          <button className={`filter-pill ${filter==='final'?'active':''}`} onClick={() => setFilter('final')}>
            Final <span className="pill-count">{counts.final}</span>
          </button>
        </div>
      </div>

      {!status.authenticated && (
        <div className="auth-warn">
          <AlertTriangle size={16} />
          Not authenticated with MLB.TV — go to Settings to add credentials before playing games.
        </div>
      )}

      {/* Main body — two columns */}
      <div className="dash-body">
        <aside className="dash-list">
          {loading && <div className="dash-empty">Loading games…</div>}
          {!loading && filteredGames.length === 0 && (
            <div className="dash-empty">
              <Tv size={40} />
              <p>No games {filter !== 'all' ? `matching "${filter}"` : 'found'}</p>
              {isToday && (
                <button className="btn" onClick={refreshGames}>Refresh</button>
              )}
            </div>
          )}
          {filteredGames.map(g => (
            <GameListItem
              key={g.game_id}
              game={g}
              isSelected={g.game_id === selectedId}
              isPlaying={status.now_playing_game_id === g.game_id}
              onClick={() => setSelectedId(g.game_id)}
            />
          ))}
        </aside>

        <section className="dash-detail">
          {selectedGame ? (
            <GameDetailPanel
              game={selectedGame}
              isPlaying={status.now_playing_game_id === selectedGame.game_id}
              onPlay={playGame}
            />
          ) : (
            <div className="dash-empty dash-empty--lg">
              <Tv size={64} />
              <p>Select a game to see stats</p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
};

export default Dashboard;
