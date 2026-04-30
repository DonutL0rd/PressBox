import React, { useEffect, useState, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { Settings as SettingsIcon, PlaySquare, Music, MonitorPlay, Save, Video, Trash2, Plus, HelpCircle, Star } from 'lucide-react';

const MLB_TEAMS = [
  { abbr: 'ARI', name: 'Arizona Diamondbacks' },
  { abbr: 'ATL', name: 'Atlanta Braves' },
  { abbr: 'BAL', name: 'Baltimore Orioles' },
  { abbr: 'BOS', name: 'Boston Red Sox' },
  { abbr: 'CHC', name: 'Chicago Cubs' },
  { abbr: 'CWS', name: 'Chicago White Sox' },
  { abbr: 'CIN', name: 'Cincinnati Reds' },
  { abbr: 'CLE', name: 'Cleveland Guardians' },
  { abbr: 'COL', name: 'Colorado Rockies' },
  { abbr: 'DET', name: 'Detroit Tigers' },
  { abbr: 'HOU', name: 'Houston Astros' },
  { abbr: 'KC',  name: 'Kansas City Royals' },
  { abbr: 'LAA', name: 'Los Angeles Angels' },
  { abbr: 'LAD', name: 'Los Angeles Dodgers' },
  { abbr: 'MIA', name: 'Miami Marlins' },
  { abbr: 'MIL', name: 'Milwaukee Brewers' },
  { abbr: 'MIN', name: 'Minnesota Twins' },
  { abbr: 'NYM', name: 'New York Mets' },
  { abbr: 'NYY', name: 'New York Yankees' },
  { abbr: 'OAK', name: 'Oakland Athletics' },
  { abbr: 'PHI', name: 'Philadelphia Phillies' },
  { abbr: 'PIT', name: 'Pittsburgh Pirates' },
  { abbr: 'SD',  name: 'San Diego Padres' },
  { abbr: 'SF',  name: 'San Francisco Giants' },
  { abbr: 'SEA', name: 'Seattle Mariners' },
  { abbr: 'STL', name: 'St. Louis Cardinals' },
  { abbr: 'TB',  name: 'Tampa Bay Rays' },
  { abbr: 'TEX', name: 'Texas Rangers' },
  { abbr: 'TOR', name: 'Toronto Blue Jays' },
  { abbr: 'WSH', name: 'Washington Nationals' },
];
import { useTvAutomator } from '../hooks/useTvAutomator';
import './Settings.css';

const Settings: React.FC = () => {
  const navigate = useNavigate();
  const { refreshStatus, refreshGames, settings, updateSetting, showAlert } = useTvAutomator();

  // MLB State
  const [mlbUsername, setMlbUsername] = useState('');
  const [mlbPassword, setMlbPassword] = useState('');
  
  // Navidrome State
  const [navUrl, setNavUrl] = useState('');
  const [navUser, setNavUser] = useState('');
  const [navPass, setNavPass] = useState('');
  
  // Initialize form fields once when settings load
  useEffect(() => {
    if (settings.mlb_username) setMlbUsername(settings.mlb_username);
    if (settings.navidrome_server_url) setNavUrl(settings.navidrome_server_url);
    if (settings.navidrome_username) setNavUser(settings.navidrome_username);
  }, [settings.mlb_username, settings.navidrome_server_url, settings.navidrome_username]);

  // Derived App Settings
  const autoStart = !!settings.auto_start;
  const defaultFeed = settings.default_feed || 'HOME';
  const strikeZone = settings.strike_zone_enabled !== false;
  const strikeZoneSize = settings.strike_zone_size || 'medium';
  const batterIntel = settings.batter_intel_enabled !== false;
  const betweenInnings = settings.between_innings_enabled !== false;
  const overlayDelay = settings.overlay_delay ?? 2;
  const cecEnabled = !!settings.cec_enabled;
  const pollInterval = settings.poll_interval || 60;
  const musicSize = settings.screensaver_music_size || 'medium';
  const scheduleScale = settings.screensaver_schedule_scale ?? 100;
  const mlbAuthenticated = settings.mlb_authenticated ?? null;

  // YouTube Channels
  const sc = settings.suggested_channels || {};
  const channels = Object.entries(sc).map(([id, name]) => ({ id, name: name as string }));

  const [newChannelId, setNewChannelId] = useState('');
  const [newChannelName, setNewChannelName] = useState('');
  const [showChannelHelp, setShowChannelHelp] = useState(false);

  const notify = useCallback((msg: string, isError = false) => {
    showAlert(msg, isError ? 'error' : 'info');
  }, [showAlert]);

  const handleMlbSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!mlbUsername || !mlbPassword) return;
    try {
      const r = await fetch('/api/settings/credentials', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ mlb_username: mlbUsername, mlb_password: mlbPassword })
      });
      const data = await r.json();
      if (data.success) {
        notify("MLB Credentials Saved & Verified!");
        setMlbPassword(''); // Clear password field for security layout
        await refreshStatus();
        setTimeout(() => { refreshGames(); }, 500);
        setTimeout(() => navigate('/'), 1000);
      } else {
        notify(data.error || "MLB Auth Failed", true);
        await refreshStatus();
      }
    } catch (err) {
      notify("Network Error", true);
    }
  };

  const handleNavSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!navUrl || !navUser || !navPass) return;
    try {
      const r = await fetch('/api/music/credentials', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ server_url: navUrl, username: navUser, password: navPass })
      });
      const data = await r.json();
      if (data.success) {
        notify(`Navidrome Connected! System v${data.version}`);
        setNavPass('');
      } else {
        notify(data.error || "Navidrome Connection Failed", true);
      }
    } catch (err) {
      notify("Network Error", true);
    }
  };

  return (
    <div className="view-container animate-in" style={{ paddingBottom: '60px' }}>
      <div className="page-header">
        <div>
          <h1 className="page-title">Settings</h1>
          <p className="page-subtitle">Configure hardware, credentials, and app behavior</p>
        </div>
      </div>

      <div className="settings-grid">
        
        {/* MLB ACCOUNT */}
        <div className="settings-card">
          <div className="settings-card-header">
            <PlaySquare size={20} color="var(--accent)" />
            <h2 className="settings-card-title">MLB.TV Credentials</h2>
            {mlbAuthenticated !== null && (
              <span style={{
                marginLeft: 'auto',
                fontSize: '0.7rem',
                fontWeight: 700,
                padding: '2px 8px',
                borderRadius: '4px',
                background: mlbAuthenticated ? 'var(--green-dim)' : 'var(--red-dim)',
                color: mlbAuthenticated ? 'var(--accent)' : 'var(--red)',
                border: `1px solid ${mlbAuthenticated ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)'}`,
              }}>
                {mlbAuthenticated ? 'AUTHENTICATED' : 'NOT AUTHENTICATED'}
              </span>
            )}
          </div>
          <form className="settings-field" onSubmit={handleMlbSave}>
            <label className="settings-label">Username / Email</label>
            <input 
              className="settings-input" 
              type="text" 
              placeholder="user@example.com"
              value={mlbUsername} 
              onChange={e => setMlbUsername(e.target.value)} 
            />
            
            <label className="settings-label" style={{marginTop: '8px'}}>Password</label>
            <input 
              className="settings-input" 
              type="password" 
              placeholder="••••••••"
              value={mlbPassword} 
              onChange={e => setMlbPassword(e.target.value)} 
            />
            
            <button className="btn btn-primary btn-save" type="submit" disabled={!mlbPassword || !mlbUsername}>
              <Save size={16} /> Save & Authenticate
            </button>
          </form>
        </div>

        {/* FAVORITE TEAMS */}
        <div className="settings-card" style={{gridColumn: '1 / -1'}}>
          <div className="settings-card-header">
            <Star size={20} color="var(--accent)" />
            <h2 className="settings-card-title">Favorite Teams</h2>
          </div>
          <div style={{fontSize: '0.8rem', color: 'var(--text-tertiary)', marginBottom: '12px'}}>
            Auto Start will use these teams to pick the right broadcast feed. Games with your favorites go live first.
          </div>
          <div style={{display: 'flex', flexWrap: 'wrap', gap: '8px'}}>
            {MLB_TEAMS.map(team => {
              const favs: string[] = settings.favorite_teams || [];
              const isFav = favs.includes(team.abbr);
              return (
                <button
                  key={team.abbr}
                  title={team.name}
                  onClick={() => {
                    const next = isFav
                      ? favs.filter(t => t !== team.abbr)
                      : [...favs, team.abbr];
                    updateSetting({ favorite_teams: next });
                  }}
                  style={{
                    padding: '5px 12px',
                    borderRadius: '6px',
                    border: `1px solid ${isFav ? 'var(--accent)' : 'var(--border-subtle)'}`,
                    background: isFav ? 'var(--accent-dim)' : 'transparent',
                    color: isFav ? 'var(--accent)' : 'var(--text-secondary)',
                    fontWeight: isFav ? 700 : 500,
                    fontSize: '0.82rem',
                    cursor: 'pointer',
                    transition: 'all 0.15s',
                  }}
                >
                  {team.abbr}
                </button>
              );
            })}
          </div>
          {((settings.favorite_teams || []) as string[]).length > 0 && (
            <div style={{marginTop: '10px', fontSize: '0.78rem', color: 'var(--text-tertiary)'}}>
              {((settings.favorite_teams || []) as string[]).length} team{((settings.favorite_teams || []) as string[]).length !== 1 ? 's' : ''} selected
            </div>
          )}
        </div>

        {/* NAVIDROME */}
        <div className="settings-card">
          <div className="settings-card-header">
            <Music size={20} color="var(--accent)" />
            <h2 className="settings-card-title">Navidrome (Screensaver Music)</h2>
          </div>
          <form className="settings-field" onSubmit={handleNavSave}>
            <label className="settings-label">Server URL</label>
            <input 
              className="settings-input" 
              type="url" 
              placeholder="http://192.168.1.100:4533"
              value={navUrl} 
              onChange={e => setNavUrl(e.target.value)} 
            />
            
            <label className="settings-label" style={{marginTop: '8px'}}>Username</label>
            <input 
              className="settings-input" 
              type="text" 
              value={navUser} 
              onChange={e => setNavUser(e.target.value)} 
            />

            <label className="settings-label" style={{marginTop: '8px'}}>Password (Not Stored in UI)</label>
            <input 
              className="settings-input" 
              type="password" 
              placeholder="••••••••"
              value={navPass} 
              onChange={e => setNavPass(e.target.value)} 
            />
            
            <button className="btn btn-primary btn-save" type="submit" disabled={!navPass}>
              <Save size={16} /> Save & Ping
            </button>
          </form>
        </div>

        {/* PLAYBACK & OVERLAYS */}
        <div className="settings-card">
          <div className="settings-card-header">
            <MonitorPlay size={20} color="var(--accent)" />
            <h2 className="settings-card-title">Playback & Overlay</h2>
          </div>
          
          <div className="settings-field-row">
            <div>
              <div className="settings-label">Auto Start Favorites</div>
              <div style={{fontSize:'0.75rem', color:'var(--text-tertiary)'}}>Play games automatically when live</div>
            </div>
            <label className="switch">
              <input type="checkbox" checked={autoStart} onChange={e => {
                updateSetting({ auto_start: e.target.checked });
              }} />
              <span className="slider"></span>
            </label>
          </div>

          <div className="settings-field-row">
            <div className="settings-label">Default Broadcast Feed</div>
            <select className="settings-input settings-select" style={{width: 'auto', minWidth: '120px'}} value={defaultFeed} onChange={e => {
              updateSetting({ default_feed: e.target.value });
            }}>
              <option value="HOME">Home</option>
              <option value="AWAY">Away</option>
            </select>
          </div>

          <hr style={{borderTop: '1px solid var(--border-subtle)', margin: '8px 0'}} />

          <div className="settings-field-row">
            <div>
              <div className="settings-label">Pitch Tracker</div>
              <div style={{fontSize:'0.75rem', color:'var(--text-tertiary)'}}>Show live pitch locations and strike zone</div>
            </div>
            <label className="switch">
              <input type="checkbox" checked={strikeZone} onChange={e => {
                updateSetting({ strike_zone_enabled: e.target.checked });
              }} />
              <span className="slider"></span>
            </label>
          </div>

          <div className="settings-field-row">
            <div className="settings-label">Tracker Size</div>
            <select className="settings-input settings-select" style={{width: 'auto'}} value={strikeZoneSize} onChange={e => {
              updateSetting({ strike_zone_size: e.target.value });
            }}>
              <option value="small">Small</option>
              <option value="medium">Medium</option>
              <option value="large">Large</option>
            </select>
          </div>

          <div className="settings-field-row">
            <div>
              <div className="settings-label">Batter Intel Card</div>
              <div style={{fontSize:'0.75rem', color:'var(--text-tertiary)'}}>Flash stats when a new batter steps up</div>
            </div>
            <label className="switch">
              <input type="checkbox" checked={batterIntel} onChange={e => {
                updateSetting({ batter_intel_enabled: e.target.checked });
              }} />
              <span className="slider"></span>
            </label>
          </div>

          <div className="settings-field-row">
            <div>
              <div className="settings-label">Between Innings Overlay</div>
              <div style={{fontSize:'0.75rem', color:'var(--text-tertiary)'}}>Show scores, due up, and pitcher stats during breaks</div>
            </div>
            <label className="switch">
              <input type="checkbox" checked={betweenInnings} onChange={e => {
                updateSetting({ between_innings_enabled: e.target.checked });
              }} />
              <span className="slider"></span>
            </label>
          </div>

          <hr style={{borderTop: '1px solid var(--border-subtle)', margin: '8px 0'}} />

          <div className="settings-field-row">
            <div>
              <div className="settings-label">Overlay Delay</div>
              <div style={{fontSize:'0.75rem', color:'var(--text-tertiary)'}}>Seconds to wait before showing overlay updates (sync with TV delay)</div>
            </div>
            <div style={{display: 'flex', alignItems: 'center', gap: '8px'}}>
              <input
                className="settings-input"
                type="number"
                min="0" max="15" step="0.5"
                value={overlayDelay}
                onChange={e => updateSetting({ overlay_delay: parseFloat(e.target.value) || 0 })}
                style={{width: '70px', textAlign: 'center'}}
              />
              <span style={{fontSize:'0.8rem', color:'var(--text-tertiary)'}}>sec</span>
            </div>
          </div>
        </div>

        {/* SYSTEM */}
        <div className="settings-card">
          <div className="settings-card-header">
            <SettingsIcon size={20} color="var(--accent)" />
            <h2 className="settings-card-title">System & Hardware</h2>
          </div>
          
          <div className="settings-field-row">
            <div>
              <div className="settings-label">HDMI CEC Control</div>
              <div style={{fontSize:'0.75rem', color:'var(--text-tertiary)'}}>Turn TV on/off automatically</div>
            </div>
            <label className="switch">
              <input type="checkbox" checked={cecEnabled} onChange={e => {
                updateSetting({ cec_enabled: e.target.checked });
              }} />
              <span className="slider"></span>
            </label>
          </div>

          <div className="settings-field-row">
            <div className="settings-label">Schedule Poll Interval</div>
            <div style={{display: 'flex', alignItems: 'center', gap: '8px'}}>
              <input 
                className="settings-input" 
                type="number" 
                min="15" max="300" 
                value={pollInterval} 
                onChange={e => updateSetting({ poll_interval: parseInt(e.target.value) || 60 })} 
                style={{width: '80px', textAlign: 'center'}}
              />
              <span style={{fontSize:'0.8rem', color:'var(--text-tertiary)'}}>sec</span>
            </div>
          </div>
          
          <div className="settings-field-row">
            <div className="settings-label">Screensaver Music UI</div>
            <select className="settings-input settings-select" style={{width: 'auto'}} value={musicSize} onChange={e => {
              updateSetting({ screensaver_music_size: e.target.value });
            }}>
              <option value="small">Small</option>
              <option value="medium">Medium</option>
              <option value="large">Large</option>
            </select>
          </div>

          <div className="settings-field-row">
            <div>
              <div className="settings-label">Schedule Scale</div>
              <div style={{fontSize:'0.75rem', color:'var(--text-tertiary)'}}>Size of the baseball schedule on the screensaver</div>
            </div>
            <div style={{display: 'flex', alignItems: 'center', gap: '8px'}}>
              <input
                className="settings-input"
                type="number"
                min="50" max="200" step="10"
                value={scheduleScale}
                onChange={e => updateSetting({ screensaver_schedule_scale: parseInt(e.target.value) || 100 })}
                style={{width: '70px', textAlign: 'center'}}
              />
              <span style={{fontSize:'0.8rem', color:'var(--text-tertiary)'}}>%</span>
            </div>
          </div>
        </div>

        {/* YOUTUBE CHANNELS */}
        <div className="settings-card" style={{gridColumn: '1 / -1'}}>
          <div className="settings-card-header">
            <Video size={20} color="var(--red)" />
            <h2 className="settings-card-title">YouTube Suggested Channels</h2>
          </div>

          <div style={{fontSize: '0.8rem', color: 'var(--text-tertiary)', lineHeight: 1.5}}>
            Videos from these channels appear on the YouTube page. Add channels by their Channel ID.
            <button
              className="btn"
              style={{marginLeft: '8px', padding: '2px 10px', fontSize: '0.75rem'}}
              onClick={() => setShowChannelHelp(!showChannelHelp)}
              type="button"
            >
              <HelpCircle size={12} /> How to find Channel ID
            </button>
          </div>

          {showChannelHelp && (
            <div style={{
              background: 'var(--accent-dim)',
              border: '1px solid var(--accent-border)',
              borderRadius: '12px',
              padding: '16px',
              fontSize: '0.82rem',
              color: 'var(--text-secondary)',
              lineHeight: 1.6,
            }}>
              <strong style={{color: 'var(--accent)'}}>Finding a YouTube Channel ID:</strong>
              <ol style={{margin: '8px 0 0 18px', display: 'flex', flexDirection: 'column', gap: '6px'}}>
                <li>Go to the YouTube channel page</li>
                <li>Click <strong>About</strong> → <strong>Share Channel</strong> → <strong>Copy Channel ID</strong></li>
                <li>Or: View the page source and search for <code style={{background: 'rgba(255,255,255,0.06)', padding: '1px 4px', borderRadius: '3px', fontFamily: 'var(--font-mono)', fontSize: '0.78rem'}}>channel_id</code></li>
                <li>Or: Use a site like <strong>commentpicker.com/youtube-channel-id.php</strong> — paste the channel URL</li>
                <li>The ID looks like: <code style={{background: 'rgba(255,255,255,0.06)', padding: '1px 4px', borderRadius: '3px', fontFamily: 'var(--font-mono)', fontSize: '0.78rem'}}>UCsBjURrPoezykLs9EqgamOA</code></li>
              </ol>
            </div>
          )}

          {/* Current channels list */}
          <div style={{display: 'flex', flexDirection: 'column', gap: '8px'}}>
            {channels.map((ch, i) => (
              <div key={ch.id} style={{
                display: 'flex', alignItems: 'center', gap: '12px',
                background: 'rgba(255,255,255,0.02)', borderRadius: '10px', padding: '10px 14px',
                border: '1px solid var(--border-subtle)',
              }}>
                <div style={{flex: 1, minWidth: 0}}>
                  <div style={{fontWeight: 600, fontSize: '0.9rem', color: 'var(--text-primary)'}}>{ch.name}</div>
                  <div style={{fontSize: '0.72rem', color: 'var(--text-tertiary)', fontFamily: 'var(--font-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap'}}>{ch.id}</div>
                </div>
                <button
                  className="btn-icon"
                  title="Remove channel"
                  onClick={() => {
                    const updated = channels.filter((_, idx) => idx !== i);
                    const obj: Record<string,string> = {};
                    updated.forEach(c => { obj[c.id] = c.name; });
                    updateSetting({ suggested_channels: obj });
                  }}
                  style={{color: 'var(--text-tertiary)', padding: '6px'}}
                >
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
            {channels.length === 0 && (
              <div style={{textAlign: 'center', padding: '16px', color: 'var(--text-tertiary)', fontSize: '0.85rem'}}>
                No channels configured
              </div>
            )}
          </div>

          {/* Add new channel */}
          <div style={{display: 'flex', gap: '8px', flexWrap: 'wrap'}}>
            <input
              className="settings-input"
              type="text"
              placeholder="Channel ID (e.g. UCsBjURrPoezykLs9EqgamOA)"
              value={newChannelId}
              onChange={e => setNewChannelId(e.target.value)}
              style={{flex: '1 1 200px', fontFamily: 'var(--font-mono)', fontSize: '0.82rem'}}
            />
            <input
              className="settings-input"
              type="text"
              placeholder="Display Name (e.g. Fireship)"
              value={newChannelName}
              onChange={e => setNewChannelName(e.target.value)}
              style={{flex: '1 1 150px'}}
            />
            <button
              className="btn btn-accent"
              disabled={!newChannelId.trim() || !newChannelName.trim()}
              onClick={() => {
                const updated = [...channels, { id: newChannelId.trim(), name: newChannelName.trim() }];
                const obj: Record<string,string> = {};
                updated.forEach(c => { obj[c.id] = c.name; });
                updateSetting({ suggested_channels: obj });
                setNewChannelId('');
                setNewChannelName('');
              }}
            >
              <Plus size={16} /> Add Channel
            </button>
          </div>
        </div>

      </div>
    </div>
  );
};

export default Settings;
