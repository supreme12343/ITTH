import { useEffect, useMemo, useRef, useState } from 'react';
import { filterCampusApps } from './appLogic';

const filters = ['All tools', 'Study', 'Create', 'Teach', 'Play'];

const iconMarks: Record<string, string> = {
  attendance: '◎', scanner: '⌁', agents: '✳', classroom: '▤', calculator: '＋',
  resume: '▧', grades: '↗', timer: '◷', translator: '文', presenter: '▹', roblox: '◈',
  'learning-arcade': '✦', 'source-ledger': '⌕', 'dataset-guide': '▦',
  'regex-workbench': '.*', 'exit-ticket-analyzer': '≋',
};

const categoryTone: Record<string, string> = {
  Study: 'mint', Create: 'lavender', Teach: 'peach', Play: 'yellow',
};

function getDateLabel() {
  return new Intl.DateTimeFormat(undefined, { weekday: 'long', month: 'long', day: 'numeric' }).format(new Date());
}

export default function App() {
  const [apps, setApps] = useState<CampusApp[]>([]);
  const [featuredIndex, setFeaturedIndex] = useState(0);
  const [carouselPaused, setCarouselPaused] = useState(false);
  const [filter, setFilter] = useState('All tools');
  const [search, setSearch] = useState('');
  const [launching, setLaunching] = useState<string | null>(null);
  const [notice, setNotice] = useState('');
  const [aboutOpen, setAboutOpen] = useState(false);
  const [theme, setTheme] = useState<'light' | 'dark'>(() => {
    try { return localStorage.getItem('campus-suite-theme') === 'dark' ? 'dark' : 'light'; }
    catch { return 'light'; }
  });
  const [themeReady, setThemeReady] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const touchStartX = useRef<number | null>(null);
  const autoDirection = useRef(1);

  useEffect(() => {
    let active = true;
    window.campusApi.getTheme().then((sharedTheme) => {
      if (active) { setTheme(sharedTheme); setThemeReady(true); }
    }).catch(() => { if (active) setThemeReady(true); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!themeReady) return;
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('campus-suite-theme', theme); } catch { /* Keep the current session theme. */ }
    window.campusApi.setTheme(theme).catch(() => {});
  }, [theme, themeReady]);

  useEffect(() => {
    window.campusApi.listApps().then((loadedApps) => {
      setApps(loadedApps);
      const classroomIndex = loadedApps.findIndex((app) => app.id === 'classroom');
      setFeaturedIndex(classroomIndex >= 0 ? classroomIndex : 0);
    }).catch(() => setNotice('Campus Suite could not load its tools.'));
  }, []);

  useEffect(() => {
    // launchApp answers as soon as the tool process starts, so a tool that dies
    // a moment later — nearly always a missing Python package — reports back here
    // instead. Without this the card just said "opening…" and nothing happened.
    return window.campusApi.onToolFailed?.((detail) => {
      setNotice(detail.error);
      setLaunching((current) => (current === detail.id ? null : current));
    });
  }, []);

  useEffect(() => {
    if (carouselPaused || apps.length < 2 || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const timer = window.setInterval(() => setFeaturedIndex((index) => {
      if (index >= apps.length - 1) autoDirection.current = -1;
      else if (index <= 0) autoDirection.current = 1;
      return index + autoDirection.current;
    }), 7000);
    return () => window.clearInterval(timer);
  }, [apps.length, carouselPaused, launching]);

  useEffect(() => {
    if (!notice) return;
    const timeout = window.setTimeout(() => setNotice(''), 3600);
    return () => window.clearTimeout(timeout);
  }, [notice]);

  useEffect(() => {
    function handleShortcut(event: KeyboardEvent) {
      const modifier = event.metaKey || event.ctrlKey;
      if (modifier && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        searchRef.current?.focus();
      } else if (modifier && event.key === '1') {
        event.preventDefault();
        setFilter('All tools');
        setSearch('');
      } else if (event.key === 'Escape') {
        setAboutOpen(false);
        searchRef.current?.blur();
      }
    }
    window.addEventListener('keydown', handleShortcut);
    return () => window.removeEventListener('keydown', handleShortcut);
  }, []);

  const readyCount = apps.filter((app) => app.available).length;
  const visibleApps = useMemo(() => {
    return filterCampusApps(apps, filter, search);
  }, [apps, filter, search]);

  async function launch(app: CampusApp) {
    if (!app.available || launching) return;
    setLaunching(app.id);
    setNotice('');
    try {
      const result = await window.campusApi.launchApp(app.id);
      setNotice(result.ok ? (result.external ? 'Opening Roblox…' : `${app.name} is opening…`) : result.error || `Could not open ${app.name}.`);
    } catch {
      setNotice(`Could not open ${app.name}. Try again in a moment.`);
    } finally {
      setLaunching(null);
    }
  }

  function openHome() { setFilter('All tools'); setSearch(''); }
  function moveFeatured(step: number) {
    if (!apps.length) return;
    setFeaturedIndex((index) => Math.max(0, Math.min(apps.length - 1, index + step)));
  }
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand-row">
          <div className="brand-mark"><span>c</span><i /></div>
          <div className="brand-name">campus<span>suite</span></div>
        </div>

        <div className="workspace-label">YOUR WORKSPACE</div>
        <nav className="side-nav" aria-label="Main navigation">
          <button className={`nav-item ${filter === 'All tools' ? 'active' : ''}`} onClick={() => { setFilter('All tools'); setSearch(''); }}>
            <span className="nav-glyph">⌂</span><span>Home</span><kbd>Ctrl 1</kbd>
          </button>
          <button className={`nav-item ${filter === 'Study' ? 'active' : ''}`} onClick={() => setFilter('Study')}>
            <span className="nav-glyph">▤</span><span>Study space</span>
          </button>
          <button className={`nav-item ${filter === 'Teach' ? 'active' : ''}`} onClick={() => setFilter('Teach')}>
            <span className="nav-glyph">◫</span><span>Teacher tools</span>
          </button>
          <button className={`nav-item ${filter === 'Create' ? 'active' : ''}`} onClick={() => setFilter('Create')}>
            <span className="nav-glyph">✳</span><span>AI & create</span>
          </button>
        </nav>

        <div className="sidebar-bottom">
          <div className="sidebar-note">
            <span className="note-orbit">✳</span>
            <strong>Good work starts small.</strong>
            <p>Pick a tool and make a little progress today.</p>
          </div>
          <button className="profile-button" onClick={() => setAboutOpen(true)}>
            <span className="avatar">CS</span>
            <span className="profile-copy"><strong>Campus Suite</strong><small>About this app</small></span>
            <span className="profile-dots">···</span>
          </button>
        </div>
      </aside>

      <main className="main-area">
        <header className="titlebar">
          <div className="drag-region"><span className="breadcrumb"><button onClick={openHome}>Workspace</button> <b>/</b> {filter === 'All tools' ? 'Home' : filter}</span></div>
          <button className="theme-toggle" aria-label={`Switch to ${theme === 'light' ? 'dark' : 'light'} mode`} aria-pressed={theme === 'dark'} onClick={() => setTheme(theme === 'light' ? 'dark' : 'light')}>
            <span aria-hidden="true">{theme === 'light' ? '☾' : '☀'}</span><span>{theme === 'light' ? 'Dark mode' : 'Light mode'}</span>
          </button>
          <div className="window-controls" aria-label="Window controls">
            <button aria-label="Minimize" onClick={() => window.campusApi.windowAction('minimize')}>−</button>
            <button aria-label="Maximize" onClick={() => window.campusApi.windowAction('toggle-maximize')}>□</button>
            <button className="close-control" aria-label="Close" onClick={() => window.campusApi.windowAction('close')}>×</button>
          </div>
        </header>

        <div className="page-content">
          <div className="greeting-row">
            <div>
              <div className="date-label">{getDateLabel()}</div>
              <h1>Your campus,<br /><em>in one place.</em></h1>
              <p className="greeting-copy">A little less switching. A lot more getting there.</p>
            </div>
            <div className="sun-stamp" aria-hidden="true"><span>✳</span><small>MAKE<br />TODAY<br />COUNT</small></div>
          </div>

          <section className="focus-carousel" aria-label="Featured tools" aria-roledescription="carousel">
            <div
              className="focus-banner"
              onMouseEnter={() => setCarouselPaused(true)}
              onMouseLeave={() => setCarouselPaused(false)}
              onFocusCapture={() => setCarouselPaused(true)}
              onBlurCapture={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setCarouselPaused(false);
              }}
              onTouchStart={(event) => { touchStartX.current = event.touches[0]?.clientX ?? null; }}
              onTouchEnd={(event) => {
                const start = touchStartX.current;
                const end = event.changedTouches[0]?.clientX;
                touchStartX.current = null;
                if (start !== null && end !== undefined && Math.abs(end - start) > 45) {
                  event.preventDefault();
                  moveFeatured(end < start ? 1 : -1);
                }
              }}
            >
              {apps.length ? <div className="focus-track" style={{ transform: `translateX(-${featuredIndex * 100}%)` }}>
                {apps.map((app, index) => (
                  <article key={app.id} className="focus-slide" data-category={app.category} aria-hidden={index !== featuredIndex} aria-roledescription="slide" aria-label={`${index + 1} of ${apps.length}`}>
                    <div className="focus-copy">
                      <span className="eyebrow"><i /> {app.category.toUpperCase()} TOOL <b>·</b> {String(index + 1).padStart(2, '0')} / {apps.length}</span>
                      <h2>{app.name}</h2>
                      <p>{app.available ? app.detail : 'This tool needs setup on this computer.'}</p>
                      <button className="primary-button" onClick={() => launch(app)} disabled={!app.available || Boolean(launching)} tabIndex={index === featuredIndex ? 0 : -1}>
                        {launching === app.id ? 'Opening…' : app.available ? `Open ${app.name}` : 'Needs setup'} <span>↗</span>
                      </button>
                    </div>
                    <div className="focus-art" aria-hidden="true">
                      <div className="focus-orbit orbit-one" /><div className="focus-orbit orbit-two" />
                      <div className="focus-sun" />
                      <span className={`tool-icon ${categoryTone[app.category] || 'mint'}`}>{iconMarks[app.id] || '✳'}</span>
                      <small>{app.category}</small>
                    </div>
                  </article>
                ))}
              </div> : <div className="focus-slide focus-slide-empty"><div className="focus-copy"><span className="eyebrow"><i /> YOUR TOOLBOX</span><h2>Getting your tools ready…</h2><p>Your featured tools will appear here.</p></div></div>}
              {apps.length > 1 && <div className="carousel-controls" aria-label="Featured tool controls">
                <span className="carousel-count" aria-live="polite">{String(featuredIndex + 1).padStart(2, '0')} <i>/</i> {String(apps.length).padStart(2, '0')}</span>
                <button aria-label="Previous tool" onClick={() => moveFeatured(-1)} disabled={featuredIndex === 0}>←</button>
                <button aria-label="Next tool" onClick={() => moveFeatured(1)} disabled={featuredIndex === apps.length - 1}>→</button>
              </div>}
            </div>
            {apps.length > 1 && <nav className="carousel-progress" aria-label="Choose a featured tool">
              {apps.map((app, index) => <button key={app.id} className={index === featuredIndex ? 'active' : ''} aria-label={`Show ${app.name}, ${index + 1} of ${apps.length}`} aria-current={index === featuredIndex ? 'step' : undefined} onClick={() => setFeaturedIndex(index)} />)}
            </nav>}
          </section>

          <section className="tools-section" aria-labelledby="tools-heading">
            <div className="section-head">
              <div><span className="section-kicker">THE TOOLBOX</span><h2 id="tools-heading">What are we working on?</h2></div>
              <div className="tool-count"><span>{readyCount}</span> ready to go</div>
            </div>

            <div className="tools-toolbar">
              <div className="filter-list" role="group" aria-label="Filter tools">
                {filters.map((item) => (
                  <button key={item} aria-pressed={filter === item} className={filter === item ? 'filter-chip selected' : 'filter-chip'} onClick={() => setFilter(item)}>{item}</button>
                ))}
              </div>
              <label className="search-box"><span aria-hidden="true">⌕</span><input ref={searchRef} value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Find a tool" aria-label="Find a tool" /><kbd>Ctrl K</kbd></label>
            </div>

            {visibleApps.length ? <div className="tool-grid">
              {visibleApps.map((app) => (
                <button key={app.id} className={`tool-card ${!app.available ? 'unavailable' : ''}`} onClick={() => launch(app)} disabled={!app.available || Boolean(launching)}>
                  <div className="card-top"><span className={`tool-icon ${categoryTone[app.category] || 'mint'}`}>{iconMarks[app.id] || '✳'}</span><span className="card-arrow">{app.available ? '↗' : '·'}</span></div>
                  <div className="card-category">{app.available ? app.category : 'NEEDS SETUP'}</div>
                  <h3>{app.name}</h3><p>{app.available ? app.detail : 'This tool is not installed yet.'}</p>
                  {launching === app.id && <span className="launch-state">Opening…</span>}
                </button>
              ))}
            </div> : <div className="empty-state"><span>⌕</span><strong>No tools found</strong><p>Try another search or category.</p><button onClick={() => { setSearch(''); setFilter('All tools'); }}>Show all tools</button></div>}
          </section>

          <footer className="page-footer"><span><i /> Your campus tools are ready when you are.</span><button onClick={() => setAboutOpen(true)}>About Campus Suite <span>↗</span></button></footer>
        </div>
      </main>

      {launching && <div className="modal-backdrop launch-backdrop" role="status" aria-live="polite" aria-label={`Opening ${apps.find((app) => app.id === launching)?.name || 'tool'}`}>
        <section className="launch-modal">
          <span className="launch-spinner" aria-hidden="true" />
          <h2>Opening {apps.find((app) => app.id === launching)?.name || 'tool'}</h2>
          <p>Getting it ready for you…</p>
        </section>
      </div>}
      {notice && <div className="toast" role="status"><span>✳</span>{notice}</div>}
      {aboutOpen && <div className="modal-backdrop" onMouseDown={(event) => event.target === event.currentTarget && setAboutOpen(false)}>
        <section className="about-modal" role="dialog" aria-modal="true" aria-labelledby="about-title">
          <button className="modal-close" aria-label="Close about window" onClick={() => setAboutOpen(false)}>×</button>
          <div className="brand-mark modal-mark"><span>c</span><i /></div>
          <div className="section-kicker">YOUR EVERYDAY CAMPUS KIT</div><h2 id="about-title">Campus Suite</h2>
          <p>Study, create, teach and take a breather with the tools already on your campus.</p>
          <div className="about-stat"><strong>{readyCount}</strong><span>tools ready on this computer</span></div>
          <button className="primary-button" onClick={() => setAboutOpen(false)}>Back to your workspace <span>↗</span></button>
        </section>
      </div>}
    </div>
  );
}
