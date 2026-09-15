import { useEffect, useState } from 'react'

const COLUMNS = [
  { key: 'first_name', label: 'First Name', width: '11%' },
  { key: 'last_name', label: 'Last Name', width: '11%' },
  { key: 'position', label: 'Position / Job Title', width: '16%' },
  { key: 'company', label: 'Company', width: '14%' },
  { key: 'location', label: 'Location', width: '13%' },
  { key: 'phone', label: 'Phone Number', width: '13%' },
  { key: 'email', label: 'Email Address', width: '16%' },
]

const blankLead = () => ({
  first_name: '',
  last_name: '',
  position: '',
  company: '',
  location: '',
  phone: '',
  email: '',
  source_file: 'manual',
})

function formatBytes(bytes) {
  if (!bytes && bytes !== 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

export default function App() {
  const [files, setFiles] = useState([])
  const [previews, setPreviews] = useState([])
  const [leads, setLeads] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [config, setConfig] = useState(null)
  const [dragOver, setDragOver] = useState(false)

  useEffect(() => {
    fetch('/api/config').then(r => r.json()).then(setConfig).catch(() => {})
  }, [])

  useEffect(() => {
    const urls = files.map(f => URL.createObjectURL(f))
    setPreviews(urls)
    return () => urls.forEach(u => URL.revokeObjectURL(u))
  }, [files])

  const addFiles = (list) => {
    const imgs = Array.from(list || []).filter(f => f.type.startsWith('image/'))
    if (imgs.length === 0) return
    setFiles(prev => [...prev, ...imgs].slice(0, 50))
    setError('')
  }

  const removeFile = (idx) => setFiles(prev => prev.filter((_, i) => i !== idx))
  const clearAll = () => { setFiles([]); setLeads([]); setError('') }

  const updateLead = (idx, key, value) => {
    setLeads(prev => prev.map((l, i) => (i === idx ? { ...l, [key]: value } : l)))
  }
  const removeLead = (idx) => setLeads(prev => prev.filter((_, i) => i !== idx))

  const extract = async () => {
    if (files.length === 0) { setError('Please select at least one business card image.'); return }
    setLoading(true); setError('')
    try {
      const form = new FormData()
      files.forEach(f => form.append('files', f, f.name))
      const res = await fetch('/api/extract', { method: 'POST', body: form })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || 'Extraction failed')
      setLeads(data.leads || [])
      if (data.errors?.length) setError(data.errors.map(e => `${e.file}: ${e.error}`).join(' · '))
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  const downloadExcel = async () => {
    if (leads.length === 0) { setError('No leads to export.'); return }
    try {
      const res = await fetch('/api/export-excel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ leads }),
      })
      if (!res.ok) throw new Error('Excel export failed')
      const blob = await res.blob()
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = 'leads.xlsx'
      document.body.appendChild(a); a.click(); a.remove()
      URL.revokeObjectURL(url)
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div className="page">
      <div className="container">
        <header className="topbar">
          <div className="brand">
            <div className="logo" aria-hidden="true">
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
                <rect x="2" y="5" width="20" height="14" rx="2.5" stroke="currentColor" strokeWidth="1.8" />
                <circle cx="8.5" cy="11" r="2" stroke="currentColor" strokeWidth="1.8" />
                <path d="M5.5 16.5c.6-1.4 1.7-2 3-2s2.4.6 3 2M14 9.5h5M14 13h5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
              </svg>
            </div>
            <div className="brand-text">
              <h1>Business Card Lead Extractor</h1>
              <p>Bulk upload → Qwen VLM extraction → structured lead list → Excel</p>
            </div>
          </div>
          <div className="status-wrap">
            {config ? (
              <span className={`status-pill ${config.configured ? 'is-ok' : 'is-warn'}`}>
                <span className="dot" />
                Qwen · {config.provider} · {config.configured ? 'ready' : 'key missing'}
              </span>
            ) : (
              <span className="status-pill"><span className="dot" />Connecting…</span>
            )}
          </div>
        </header>

        {config && !config.configured && (
          <div className="notice">
            <strong>Demo mode:</strong>&nbsp;no API key detected for provider “{config.provider}”.
            Extraction returns labeled sample data. Set the key in <code>.env</code> for live Qwen results.
          </div>
        )}

        <section className="card">
          <div className="card-head">
            <span className="step">1</span>
            <h2>Upload business cards</h2>
            <span className="spacer" />
            <span className="count-pill">{files.length} / 50 files</span>
          </div>

          <div
            className={`dropzone ${dragOver ? 'is-over' : ''}`}
            role="button"
            tabIndex={0}
            onDragOver={e => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={e => { e.preventDefault(); setDragOver(false); addFiles(e.dataTransfer.files) }}
            onClick={() => document.getElementById('file-input').click()}
            onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') document.getElementById('file-input').click() }}
          >
            <div className="drop-inner">
              <div className="drop-icon" aria-hidden="true">
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
                  <path d="M12 16V4m0 0L7 9m5-5l5 5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M4 17v2.5A1.5 1.5 0 0 0 5.5 21h13a1.5 1.5 0 0 0 1.5-1.5V17" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                </svg>
              </div>
              <p className="drop-title">Drag &amp; drop card images here, or <span className="link">browse files</span></p>
              <p className="drop-hint">JPG · PNG · WEBP — up to 50 files, 10 MB each</p>
            </div>
            <input
              id="file-input" type="file" accept="image/*" multiple hidden
              onChange={e => { addFiles(e.target.files); e.target.value = '' }}
            />
          </div>

          {previews.length > 0 && (
            <div className="thumbs">
              {previews.map((url, i) => (
                <div className="thumb" key={`${files[i]?.name}-${i}`}>
                  <div className="thumb-img"><img src={url} alt={files[i]?.name || `card ${i + 1}`} /></div>
                  <div className="thumb-meta">
                    <div className="thumb-text">
                      <span className="thumb-name" title={files[i]?.name}>{files[i]?.name}</span>
                      <span className="thumb-size">{formatBytes(files[i]?.size)}</span>
                    </div>
                    <button type="button" className="icon-btn" onClick={(e) => { e.stopPropagation(); removeFile(i) }} aria-label={`Remove ${files[i]?.name}`}>×</button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="actions">
            <button type="button" className="btn primary" onClick={extract} disabled={loading || files.length === 0}>
              {loading && <span className="spinner" aria-hidden="true" />}
              {loading ? 'Extracting with Qwen…' : `Extract leads from ${files.length} card${files.length === 1 ? '' : 's'}`}
            </button>
            <button type="button" className="btn ghost" onClick={clearAll} disabled={loading || (files.length === 0 && leads.length === 0)}>Clear</button>
          </div>
          {error && <div className="alert" role="alert">{error}</div>}
        </section>

        <section className="card">
          <div className="card-head">
            <span className="step">2</span>
            <h2>Extracted leads</h2>
            <span className="spacer" />
            <span className="count-pill">{leads.length} lead{leads.length === 1 ? '' : 's'}</span>
          </div>

          {leads.length === 0 ? (
            <div className="empty">
              <div className="empty-icon" aria-hidden="true">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none">
                  <path d="M8 6h13M8 12h13M8 18h13" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
                  <circle cx="4" cy="6" r="1.2" fill="currentColor" /><circle cx="4" cy="12" r="1.2" fill="currentColor" /><circle cx="4" cy="18" r="1.2" fill="currentColor" />
                </svg>
              </div>
              <p className="empty-title">No leads yet</p>
              <p className="empty-hint">Upload card images above and click “Extract leads”.</p>
            </div>
          ) : (
            <>
              <div className="table-scroll">
                <table className="grid">
                  <colgroup>
                    {COLUMNS.map(c => <col key={c.key} style={{ width: c.width }} />)}
                    <col style={{ width: '6%' }} />
                  </colgroup>
                  <thead>
                    <tr>
                      {COLUMNS.map(c => <th key={c.key} scope="col">{c.label}</th>)}
                      <th scope="col" className="th-action"><span className="sr">Actions</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {leads.map((lead, i) => (
                      <tr key={i}>
                        {COLUMNS.map(c => (
                          <td key={c.key}>
                            <input
                              className="cell"
                              value={lead[c.key] || ''}
                              placeholder="—"
                              aria-label={`${c.label} row ${i + 1}`}
                              onChange={e => updateLead(i, c.key, e.target.value)}
                            />
                          </td>
                        ))}
                        <td className="td-action">
                          <button type="button" className="icon-btn danger" onClick={() => removeLead(i)} aria-label={`Delete row ${i + 1}`} title="Delete row">×</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="actions between">
                <button type="button" className="btn ghost" onClick={() => setLeads(prev => [...prev, blankLead()])}>+ Add row</button>
                <button type="button" className="btn primary" onClick={downloadExcel}>
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                    <path d="M12 4v12m0 0l-4.5-4.5M12 16l4.5-4.5M4 20h16" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                  Download Excel (.xlsx)
                </button>
              </div>
              <p className="foot-note">Tip: click any cell to correct a value before exporting. Source file: {leads[0]?.source_file || '—'}{leads.length > 1 ? ` (+${leads.length - 1} more)` : ''}</p>
            </>
          )}
        </section>

        <footer className="footer">
          FastAPI + Qwen VLM (DashScope · OpenRouter · HuggingFace · Groq · Ollama · mock) · Excel via openpyxl
        </footer>
      </div>
    </div>
  )
}
