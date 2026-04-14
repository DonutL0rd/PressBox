import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Settings as SettingsIcon, PlaySquare, Music, MonitorPlay, Save, Video, Trash2, Plus, HelpCircle } from 'lucide-react';
import { useTvAutomator } from '../hooks/useTvAutomator';
import './Settings.css';

const Settings: React.FC = () => {
  const navigate = useNavigate();
  const { refreshStatus, refreshGames } = useTvAutomator();

  // MLB State
  const [mlbUsername, setMlbUsername] = useState('');
  const [mlbPassword, setMlbPassword] = useState('');
  
  // Navidrome State
  const [navUrl, setNavUrl] = useState('');
  const [navUser, setNavUser] = useState('');
  const [navPass, setNavPass] = useState('');
  
  // App Settings
  const [autoStart, setAutoStart] = useState(false);
  const [defaultFeed, setDefaultFeed] = useState('HOME');
  const [strikeZone, setStrikeZone] = useState(true);
  const [strikeZoneSize, setStrikeZoneSize] = useState('medium');
  const [cecEnabled, setCecEnabled] = useState(false);
  const [pollInterval, setPollInterval] = useState(60);
  const [musicSize, setMusicSize] = useState('medium');

  // YouTube Channels
  const [channels, setChannels] = useState<{id: string, name: string}[]>([]);
  const [newChannelId, setNewChannelId] = useState('');
  const [newChannelName, setNewChannelName] = useState('');
  const [showChannelHelp, setShowChannelHelp] = useState(false);

  // Loading / Messages
  const [toast, setToast] = useState<{msg: string, isError: boolean} | null>(null);
  const [mlbAuthenticated, setMlbAuthenticated] = useState<boolean | null>(null);

  useEffect(() => {
    fetch('/api/settings')
      .then(r => r.json())
      .then(data => {
        setMlbUsername(data.mlb_username || '');
        setMlbAuthenticated(!!data.mlb_authenticated);
        setAutoStart(!!data.auto_start);
        setDefaultFeed(data.default_feed || 'HOME');
        setStrikeZone(!!data.strike_zone_enabled);
        setStrikeZoneSize(data.strike_zone_size || 'medium');
        setCecEnabled(!!data.cec_enabled);
        setPollInterval(data.poll_interval || 60);
        setMusicSize(data.screensaver_music_size || 'medium');
        
        setNavUrl(data.navidrome_server_url || '');
        setNavUser(data.navidrome_username || '');

        // YouTube suggested channels {channel_id: display_name}
        const sc = data.suggested_channels || {};
        setChannels(Object.entries(sc).map(([id, name]) => ({ id, name: name as string })));
      })
      .catch(err => console.error("Failed to load settings:", err));
  }, []);

  const showToast = (msg: string, isError = false) => {
    setToast({ msg, isError });
    setTimeout(() => setToast(null), 4000);
  };

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
        showToast("MLB Credentials Saved & Verified!");
        setMlbAuthenticated(true);
        setMlbPassword(''); // Clear password field for security layout
        // Immediately pull fresh status + games so the TopBar "Auth OK" and
        // Dashboard games populate without waiting on the WebSocket.
        await refreshStatus();
        // Give the backend scheduler a moment to finish refreshing, then pull games.
        setTimeout(() => { refreshGames(); }, 500);
        setTimeout(() => navigate('/'), 1000); // Navigate to Dashboard after toast
      } else {
        showToast(data.error || "MLB Auth Failed", true);
        setMlbAuthenticated(false);
        await refreshStatus();
      }
    } catch (err) {
      showToast("Network Error", true);
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
        showToast(`Navidrome Connected! System v${data.version}`);
        setNavPass('');
      } else {
        showToast(data.error || "Navidrome Connection Failed", true);
      }
    } catch (err) {
      showToast("Network Error", true);
    }
  };

  const updateSetting = async (payload: any) => {
    try {
      await fetch('/api/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      showToast("Setting updated");
    } catch (err) {
      showToast("Failed to save setting", true);
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

      {toast && (
        <div style={{
          position: 'fixed', top: 20, right: 30, zIndex: 100,
          background: toast.isError ? 'rgba(255, 42, 95, 0.9)' : 'rgba(0, 255, 170, 0.9)',
          color: toast.isError ? '#fff' : '#030308',
          padding: '12px 24px', borderRadius: '8px', fontWeight: 600,
          boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
          animation: 'fade-in-up 0.2s ease-out'
        }}>
          {toast.msg}
        </div>
      )}

      <div className="settings-grid">
        
        {/* MLB ACCOUNT */}
        <div className="settings-card glass-panel">
          <div className="settings-card-header">
            <PlaySquare size={20} color="var(--neon-cyan)" />
            <h2 className="settings-card-title">MLB.TV Credentials</h2>
            {mlbAuthenticated !== null && (
              <span style={{
                marginLeft: 'auto',
                fontSize: '0.7rem',
                fontWeight: 700,
                padding: '2px 8px',
                borderRadius: '4px',
                background: mlbAuthenticated ? 'rgba(0,255,170,0.15)' : 'rgba(255,42,95,0.15)',
                color: mlbAuthenticated ? 'var(--neon-cyan)' : '#ff2a5f',
                border: `1px solid ${mlbAuthenticated ? 'var(--neon-cyan)' : '#ff2a5f'}`,
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

        {/* NAVIDROME */}
        <div className="settings-card glass-panel">
          <div className="settings-card-header">
            <Music size={20} color="var(--neon-cyan)" />
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
        <div className="settings-card glass-panel">
          <div className="settings-card-header">
            <MonitorPlay size={20} color="var(--neon-cyan)" />
            <h2 className="settings-card-title">Playback & Overlay</h2>
          </div>
          
          <div className="settings-field-row">
            <div>
              <div className="settings-label">Auto Start Favorites</div>
              <div style={{fontSize:'0.75rem', color:'var(--text-tertiary)'}}>Play games automatically when live</div>
            </div>
            <label className="switch">
              <input type="checkbox" checked={autoStart} onChange={e => {
                setAutoStart(e.target.checked);
                updateSetting({ auto_start: e.target.checked });
              }} />
              <span className="slider"></span>
            </label>
          </div>

          <div className="settings-field-row">
            <div className="settings-label">Default Broadcast Feed</div>
            <select className="settings-input settings-select" style={{width: 'auto', minWidth: '120px'}} value={defaultFeed} onChange={e => {
              setDefaultFeed(e.target.value);
              updateSetting({ default_feed: e.target.value });
            }}>
              <option value="HOME">Home</option>
              <option value="AWAY">Away</option>
            </select>
          </div>

          <hr style={{borderTop: '1px solid var(--border-subtle)', margin: '8px 0'}} />

          <div className="settings-field-row">
            <div>
              <div className="settings-label">Strike Zone Overlay</div>
              <div style={{fontSize:'0.75rem', color:'var(--text-tertiary)'}}>Show live pitch locations</div>
            </div>
            <label className="switch">
              <input type="checkbox" checked={strikeZone} onChange={e => {
                setStrikeZone(e.target.checked);
                updateSetting({ strike_zone_enabled: e.target.checked });
              }} />
              <span className="slider"></span>
            </label>
          </div>

          <div className="settings-field-row">
            <div className="settings-label">Strike Zone Size</div>
            <select className="settings-input settings-select" style={{width: 'auto'}} value={strikeZoneSize} onChange={e => {
              setStrikeZoneSize(e.target.value);
              updateSetting({ strike_zone_size: e.target.value });
            }}>
              <option value="small">Small</option>
              <option value="medium">Medium</option>
              <option value="large">Large</option>
            </select>
          </div>
        </div>

        {/* SYSTEM */}
        <div className="settings-card glass-panel">
          <div className="settings-card-header">
            <SettingsIcon size={20} color="var(--neon-cyan)" />
            <h2 className="settings-card-title">System & Hardware</h2>
          </div>
          
          <div className="settings-field-row">
            <div>
              <div className="settings-label">HDMI CEC Control</div>
              <div style={{fontSize:'0.75rem', color:'var(--text-tertiary)'}}>Turn TV on/off automatically</div>
            </div>
            <label className="switch">
              <input type="checkbox" checked={cecEnabled} onChange={e => {
                setCecEnabled(e.target.checked);
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
                onChange={e => setPollInterval(parseInt(e.target.value) || 60)} 
                onBlur={e => updateSetting({ poll_interval: parseInt(e.target.value) || 60 })}
                style={{width: '80px', textAlign: 'center'}}
              />
              <span style={{fontSize:'0.8rem', color:'var(--text-tertiary)'}}>sec</span>
            </div>
          </div>
          
          <div className="settings-field-row">
            <div className="settings-label">Screensaver Music UI</div>
            <select className="settings-input settings-select" style={{width: 'auto'}} value={musicSize} onChange={e => {
              setMusicSize(e.target.value);
              updateSetting({ screensaver_music_size: e.target.value });
            }}>
              <option value="small">Small</option>
              <option value="medium">Medium</option>
              <option value="large">Large</option>
            </select>
          </div>
        </div>

        {/* YOUTUBE CHANNELS */}
        <div className="settings-card glass-panel" style={{gridColumn: '1 / -1'}}>
          <div className="settings-card-header">
            <Video size={20} color="var(--neon-red)" />
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
              background: 'rgba(0, 229, 255, 0.05)',
              border: '1px solid rgba(0, 229, 255, 0.2)',
              borderRadius: '12px',
              padding: '16px',
              fontSize: '0.82rem',
              color: 'var(--text-secondary)',
              lineHeight: 1.6,
            }}>
              <strong style={{color: 'var(--neon-cyan)'}}>Finding a YouTube Channel ID:</strong>
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
                    setChannels(updated);
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
              className="btn btn-neon"
              disabled={!newChannelId.trim() || !newChannelName.trim()}
              onClick={() => {
                const updated = [...channels, { id: newChannelId.trim(), name: newChannelName.trim() }];
                setChannels(updated);
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
