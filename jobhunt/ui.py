import http.server
import json
import urllib.parse
from pathlib import Path
from .store import Store

class JobHuntUIHandler(http.server.SimpleHTTPRequestHandler):
    store = Store()
    
    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.end_headers()
            
            html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>JobHunt Tracker</title>
    <style>
        :root {
            --bg: #0f1115;
            --card: #171a21;
            --line: #262b36;
            --text: #e6e8ec;
            --muted: #8b93a3;
            --accent: #7c9cff;
            --success: #3fb950;
            --warning: #d29922;
            --danger: #f85149;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            margin: 0;
            padding: 20px;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        h1 {
            font-size: 28px;
            font-weight: 800;
            margin-bottom: 24px;
        }
        
        .filters {
            display: flex;
            gap: 12px;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--line);
        }
        .filter-btn {
            background: transparent;
            color: var(--muted);
            border: none;
            font-size: 15px;
            cursor: pointer;
            padding: 6px 4px;
            font-weight: 600;
            transition: color 0.2s ease;
        }
        .filter-btn:hover {
            color: var(--text);
        }
        .filter-btn.active {
            color: var(--text);
            border-bottom: 2px solid var(--accent);
        }

        #jobs {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
            gap: 20px;
        }

        .job-card {
            background: var(--card);
            border: 1px solid var(--line);
            border-radius: 12px;
            padding: 18px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            transition: transform 0.2s ease, box-shadow 0.2s ease;
            border-left-width: 4px;
        }
        .job-card:hover {
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(0,0,0,0.2);
        }
        
        /* Status Color Coding */
        .status-tracked { 
            border-left-color: var(--line); 
        }
        .status-applied { 
            border-left-color: var(--accent); 
            background: rgba(124, 156, 255, 0.04);
            border-color: rgba(124, 156, 255, 0.2);
        }
        .status-rejected { 
            border-left-color: var(--danger); 
            background: rgba(248, 81, 73, 0.04);
            border-color: rgba(248, 81, 73, 0.15);
            opacity: 0.7;
        }
        .status-approved { 
            border-left-color: var(--success); 
            background: rgba(63, 185, 80, 0.04);
            border-color: rgba(63, 185, 80, 0.2);
        }

        .job-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            margin-bottom: 12px;
        }
        .job-title {
            font-size: 16px;
            font-weight: 700;
            line-height: 1.3;
        }
        .job-title a {
            color: var(--text);
            text-decoration: none;
        }
        .job-title a:hover {
            color: var(--accent);
            text-decoration: underline;
        }
        .badge {
            font-size: 12px;
            font-weight: 700;
            padding: 2px 8px;
            border-radius: 999px;
            color: var(--bg);
            margin-left: 12px;
            flex-shrink: 0;
        }
        .score-high { background: var(--success); }
        .score-med { background: var(--warning); }
        .score-low { background: var(--muted); }
        
        .job-meta {
            font-size: 14px;
            color: var(--text);
            margin-bottom: 8px;
            font-weight: 500;
        }
        .job-meta span { color: var(--muted); font-weight: normal; }
        
        .job-reason {
            font-size: 13px;
            color: var(--muted);
            margin-bottom: 16px;
            line-height: 1.4;
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }

        .actions {
            margin-top: auto;
            border-top: 1px solid var(--line);
            padding-top: 14px;
        }
        select {
            width: 100%;
            background: var(--bg);
            color: var(--text);
            border: 1px solid var(--line);
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 14px;
            outline: none;
            cursor: pointer;
            font-weight: 600;
        }
        select:focus {
            border-color: var(--accent);
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>JobHunt Tracker</h1>
        
        <div class="filters">
            <button class="filter-btn active" data-status="all">All Postings</button>
            <button class="filter-btn" data-status="tracked">Tracked</button>
            <button class="filter-btn" data-status="applied">Applied</button>
            <button class="filter-btn" data-status="rejected">Rejected</button>
            <button class="filter-btn" data-status="approved">Approved</button>
        </div>
        
        <div id="jobs">Loading...</div>
    </div>

    <script>
        let allJobs = [];
        let currentFilter = 'all';

        async function fetchJobs() {
            const res = await fetch('/api/jobs');
            const data = await res.json();
            
            allJobs = Object.entries(data).map(([id, job]) => ({ id, ...job }))
                .sort((a, b) => {
                    if (b.score !== a.score) return (b.score || 0) - (a.score || 0);
                    return new Date(b.first_seen) - new Date(a.first_seen);
                });
            
            renderJobs();
        }

        async function updateStatus(jobId, status) {
            await fetch(`/api/jobs/${encodeURIComponent(jobId)}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status })
            });
            const job = allJobs.find(j => j.id === jobId);
            if (job) job.status = status;
            renderJobs();
        }

        function renderJobs() {
            const container = document.getElementById('jobs');
            container.innerHTML = '';
            
            const filtered = currentFilter === 'all' 
                ? allJobs 
                : allJobs.filter(j => (j.status || (j.applied ? 'applied' : 'tracked')) === currentFilter);
                
            if (filtered.length === 0) {
                container.innerHTML = '<div style="color:var(--muted)">No jobs found in this category.</div>';
                return;
            }

            filtered.forEach(job => {
                const card = document.createElement('div');
                const status = job.status || (job.applied ? 'applied' : 'tracked');
                card.className = `job-card status-${status}`;
                
                let scoreClass = 'score-low';
                if (job.score >= 8.5) scoreClass = 'score-high';
                else if (job.score >= 7) scoreClass = 'score-med';
                
                const reason = job.reason ? `<div class="job-reason">${job.reason}</div>` : '';
                
                card.innerHTML = `
                    <div class="job-header">
                        <div class="job-title">
                            <a href="${job.url}" target="_blank">${job.title}</a>
                        </div>
                        ${job.score ? `<div class="badge ${scoreClass}">${job.score.toFixed(1)}</div>` : ''}
                    </div>
                    <div class="job-meta">
                        ${job.company} <span>&middot; ${job.location || 'Unknown'}</span>
                    </div>
                    ${reason}
                    <div class="actions">
                        <select onchange="updateStatus('${job.id}', this.value)">
                            <option value="tracked" ${status === 'tracked' ? 'selected' : ''}>Tracked</option>
                            <option value="applied" ${status === 'applied' ? 'selected' : ''}>Applied</option>
                            <option value="rejected" ${status === 'rejected' ? 'selected' : ''}>Rejected</option>
                            <option value="approved" ${status === 'approved' ? 'selected' : ''}>Approved</option>
                        </select>
                    </div>
                `;
                container.appendChild(card);
            });
        }

        document.querySelectorAll('.filter-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                e.target.classList.add('active');
                currentFilter = e.target.dataset.status;
                renderJobs();
            });
        });

        fetchJobs();
    </script>
</body>
</html>"""
            self.wfile.write(html.encode("utf-8"))
            return

        if self.path == '/api/jobs':
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(self.store.data).encode("utf-8"))
            return

        self.send_error(404)

    def do_POST(self):
        if self.path.startswith('/api/jobs/'):
            job_id = urllib.parse.unquote(self.path.split('/')[-1])
            length = int(self.headers.get('content-length', 0))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            
            if 'status' in body:
                self.store.update_status(job_id, body['status'])
                self.send_response(200)
                self.send_header("Content-type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
                return
                
        self.send_error(400)

def serve():
    port = 8080
    server = http.server.HTTPServer(("", port), JobHuntUIHandler)
    print(f"Starting JobHunt UI on http://localhost:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
