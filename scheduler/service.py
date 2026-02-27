#!/usr/bin/env python3
"""
Unified Storage Monitoring - Scheduler Service
Manages scheduled collection jobs with API for control
"""

import os
import json
import time
import threading
import subprocess
import logging
from datetime import datetime
from flask import Flask, jsonify, request

app = Flask(__name__)

# Configuration
JOBS_FILE = os.environ.get('JOBS_FILE', '/app/config/jobs.json')
COLLECTORS_DIR = os.environ.get('COLLECTORS_DIR', '/app/collectors')
LOG_DIR = os.environ.get('LOG_DIR', '/app/logs/scheduler')

# Ensure log directory exists
os.makedirs(LOG_DIR, exist_ok=True)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(LOG_DIR, 'scheduler.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Default job definitions
DEFAULT_JOBS = {
    'pure_metrics': {
        'name': 'Pure Metrics Collector',
        'description': 'Collects performance metrics from Pure Storage arrays',
        'vendor': 'pure',
        'collector': 'metrics',
        'script': 'collectors/pure/metrics.py',
        'interval': 300,
        'enabled': True
    },
    'pure_volumes': {
        'name': 'Pure Volumes Collector',
        'description': 'Collects volume and host inventory from Pure Storage arrays',
        'vendor': 'pure',
        'collector': 'volumes',
        'script': 'collectors/pure/volumes.py',
        'interval': 900,
        'enabled': True
    },
    'pure_alerts': {
        'name': 'Pure Alerts Collector',
        'description': 'Collects alerts from Pure Storage arrays',
        'vendor': 'pure',
        'collector': 'alerts',
        'script': 'collectors/pure/alerts.py',
        'interval': 300,
        'enabled': True
    }
}

# Runtime state
jobs = {}
job_threads = {}
job_lock = threading.Lock()


def load_jobs():
    """Load job definitions from file or use defaults"""
    global jobs
    
    if os.path.exists(JOBS_FILE):
        try:
            with open(JOBS_FILE, 'r') as f:
                saved = json.load(f)
                # Merge with defaults
                jobs = DEFAULT_JOBS.copy()
                for job_id, job_data in saved.items():
                    if job_id in jobs:
                        jobs[job_id].update(job_data)
                    else:
                        jobs[job_id] = job_data
                logger.info(f"Loaded {len(jobs)} jobs from {JOBS_FILE}")
        except Exception as e:
            logger.error(f"Error loading jobs: {e}")
            jobs = DEFAULT_JOBS.copy()
    else:
        jobs = DEFAULT_JOBS.copy()
        logger.info(f"Using {len(jobs)} default jobs")
    
    # Initialize runtime state
    for job_id in jobs:
        jobs[job_id].setdefault('running', False)
        jobs[job_id].setdefault('last_run', None)
        jobs[job_id].setdefault('last_status', None)
        jobs[job_id].setdefault('last_duration', None)
        jobs[job_id].setdefault('next_run', None)
        jobs[job_id].setdefault('run_count', 0)
        jobs[job_id].setdefault('error_count', 0)


def save_jobs():
    """Save job definitions to file"""
    try:
        os.makedirs(os.path.dirname(JOBS_FILE), exist_ok=True)
        
        # Save only persistent settings (not runtime state)
        save_data = {}
        for job_id, job in jobs.items():
            save_data[job_id] = {
                'name': job.get('name'),
                'description': job.get('description'),
                'vendor': job.get('vendor'),
                'collector': job.get('collector'),
                'script': job.get('script'),
                'interval': job.get('interval'),
                'enabled': job.get('enabled')
            }
        
        with open(JOBS_FILE, 'w') as f:
            json.dump(save_data, f, indent=2)
        logger.info(f"Saved {len(save_data)} jobs to {JOBS_FILE}")
    except Exception as e:
        logger.error(f"Error saving jobs: {e}")


def run_job(job_id: str) -> dict:
    """Execute a collector job"""
    job = jobs.get(job_id)
    if not job:
        return {'success': False, 'error': 'Job not found'}
    
    with job_lock:
        if job['running']:
            return {'success': False, 'error': 'Job already running'}
        job['running'] = True
    
    start_time = datetime.now()
    result = {'success': False, 'job_id': job_id, 'start_time': start_time.isoformat()}
    
    try:
        # Find script
        script_path = os.path.join('/app', job['script'])
        if not os.path.exists(script_path):
            script_path = os.path.join(COLLECTORS_DIR, job['vendor'], f"{job['collector']}.py")
        
        if not os.path.exists(script_path):
            raise FileNotFoundError(f"Script not found: {job['script']}")
        
        logger.info(f"Running job {job_id}: {script_path}")
        
        # Set up environment
        env = os.environ.copy()
        env['PYTHONPATH'] = f"/app:{env.get('PYTHONPATH', '')}"
        
        # Execute
        proc = subprocess.run(
            ['python3', script_path, '--all'],
            capture_output=True,
            text=True,
            timeout=600,
            cwd='/app',
            env=env
        )
        
        result['success'] = proc.returncode == 0
        result['stdout'] = proc.stdout[-1000:] if proc.stdout else ''  # Last 1000 chars
        result['stderr'] = proc.stderr[-1000:] if proc.stderr else ''
        result['return_code'] = proc.returncode
        
        with job_lock:
            job['last_status'] = 'success' if result['success'] else f'error: exit {proc.returncode}'
            job['run_count'] += 1
            if not result['success']:
                job['error_count'] += 1
        
        logger.info(f"Job {job_id} completed: {'success' if result['success'] else 'failed'}")
        
    except subprocess.TimeoutExpired:
        result['error'] = 'Timeout'
        with job_lock:
            job['last_status'] = 'error: timeout'
            job['error_count'] += 1
        logger.error(f"Job {job_id} timed out")
        
    except Exception as e:
        result['error'] = str(e)
        with job_lock:
            job['last_status'] = f'error: {str(e)}'
            job['error_count'] += 1
        logger.error(f"Job {job_id} error: {e}")
    
    finally:
        end_time = datetime.now()
        result['end_time'] = end_time.isoformat()
        result['duration'] = (end_time - start_time).total_seconds()
        
        with job_lock:
            job['running'] = False
            job['last_run'] = start_time.isoformat()
            job['last_duration'] = result['duration']
    
    return result


def scheduler_loop(job_id: str):
    """Background loop for a scheduled job"""
    logger.info(f"Scheduler started for job: {job_id}")
    
    while True:
        job = jobs.get(job_id)
        if not job or not job.get('enabled'):
            logger.info(f"Scheduler stopping for job: {job_id}")
            break
        
        # Calculate next run
        interval = job.get('interval', 300)
        with job_lock:
            job['next_run'] = datetime.now().isoformat()
        
        # Run the job
        run_job(job_id)
        
        # Sleep with interruptibility
        for _ in range(interval):
            if not jobs.get(job_id, {}).get('enabled', False):
                break
            time.sleep(1)
    
    with job_lock:
        if job_id in job_threads:
            del job_threads[job_id]


def start_scheduler(job_id: str):
    """Start the scheduler for a job"""
    if job_id in job_threads and job_threads[job_id].is_alive():
        return False
    
    thread = threading.Thread(target=scheduler_loop, args=(job_id,), daemon=True)
    job_threads[job_id] = thread
    thread.start()
    return True


def start_all_schedulers():
    """Start schedulers for all enabled jobs"""
    for job_id, job in jobs.items():
        if job.get('enabled'):
            start_scheduler(job_id)


# ============== API ROUTES ==============

@app.route('/health')
def health():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'jobs_loaded': len(jobs),
        'active_threads': len([t for t in job_threads.values() if t.is_alive()])
    })


@app.route('/api/jobs')
def get_jobs():
    """Get all job definitions and status"""
    with job_lock:
        job_list = []
        for job_id, job in jobs.items():
            job_list.append({
                'id': job_id,
                'name': job.get('name'),
                'description': job.get('description'),
                'vendor': job.get('vendor'),
                'collector': job.get('collector'),
                'interval': job.get('interval'),
                'enabled': job.get('enabled'),
                'running': job.get('running'),
                'last_run': job.get('last_run'),
                'last_status': job.get('last_status'),
                'last_duration': job.get('last_duration'),
                'next_run': job.get('next_run'),
                'run_count': job.get('run_count', 0),
                'error_count': job.get('error_count', 0),
                'thread_active': job_id in job_threads and job_threads[job_id].is_alive()
            })
        return jsonify({'jobs': job_list})


@app.route('/api/jobs/<job_id>')
def get_job(job_id):
    """Get single job details"""
    job = jobs.get(job_id)
    if not job:
        return jsonify({'error': 'Job not found'}), 404
    
    with job_lock:
        return jsonify({
            'id': job_id,
            **job,
            'thread_active': job_id in job_threads and job_threads[job_id].is_alive()
        })


@app.route('/api/jobs/<job_id>/run', methods=['POST'])
def run_job_api(job_id):
    """Run a job immediately"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    # Run in background
    def run_async():
        run_job(job_id)
    
    thread = threading.Thread(target=run_async, daemon=True)
    thread.start()
    
    return jsonify({'status': 'started', 'job_id': job_id})


@app.route('/api/jobs/<job_id>/enable', methods=['POST'])
def enable_job(job_id):
    """Enable a job and start its scheduler"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    with job_lock:
        jobs[job_id]['enabled'] = True
    
    start_scheduler(job_id)
    save_jobs()
    
    return jsonify({'status': 'enabled', 'job_id': job_id})


@app.route('/api/jobs/<job_id>/disable', methods=['POST'])
def disable_job(job_id):
    """Disable a job (scheduler will stop on next iteration)"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    with job_lock:
        jobs[job_id]['enabled'] = False
    
    save_jobs()
    
    return jsonify({'status': 'disabled', 'job_id': job_id})


@app.route('/api/jobs/<job_id>/interval', methods=['POST'])
def set_interval(job_id):
    """Update job interval"""
    if job_id not in jobs:
        return jsonify({'error': 'Job not found'}), 404
    
    data = request.get_json() or {}
    interval = data.get('interval')
    
    if not interval or not isinstance(interval, int) or interval < 60:
        return jsonify({'error': 'Invalid interval (minimum 60 seconds)'}), 400
    
    with job_lock:
        jobs[job_id]['interval'] = interval
    
    save_jobs()
    
    return jsonify({'status': 'updated', 'job_id': job_id, 'interval': interval})


@app.route('/api/stats')
def get_stats():
    """Get scheduler statistics"""
    with job_lock:
        total_runs = sum(j.get('run_count', 0) for j in jobs.values())
        total_errors = sum(j.get('error_count', 0) for j in jobs.values())
        active_jobs = len([j for j in jobs.values() if j.get('enabled')])
        running_jobs = len([j for j in jobs.values() if j.get('running')])
        
        return jsonify({
            'total_jobs': len(jobs),
            'active_jobs': active_jobs,
            'running_jobs': running_jobs,
            'total_runs': total_runs,
            'total_errors': total_errors,
            'uptime': 'N/A'  # TODO: track uptime
        })


# ============== MAIN ==============

if __name__ == '__main__':
    logger.info("Starting Scheduler Service")
    
    # Load jobs
    load_jobs()
    
    # Start schedulers for enabled jobs
    start_all_schedulers()
    
    # Run Flask API
    port = int(os.environ.get('SCHEDULER_PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
