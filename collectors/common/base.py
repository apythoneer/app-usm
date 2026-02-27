#!/usr/bin/env python3
"""
Base Collector Class
Abstract base class for all storage array collectors
"""

import os
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List, Optional, Any


class BaseCollector(ABC):
    """
    Abstract base class for storage collectors.
    Inherit from this class to create collectors for different vendors.
    """
    
    # Override in subclasses
    VENDOR_NAME = "Unknown"
    COLLECTOR_TYPE = "base"
    
    def __init__(self, array_name: str, log_dir: str = None):
        self.array_name = array_name
        self.start_time = None
        self.end_time = None
        self.errors = []
        self.stats = {}
        
        # Setup logging
        log_dir = log_dir or os.environ.get('LOG_DIR', '/app/logs/collectors')
        os.makedirs(log_dir, exist_ok=True)
        
        self.logger = logging.getLogger(f"{self.VENDOR_NAME}.{self.COLLECTOR_TYPE}.{array_name}")
        if not self.logger.handlers:
            handler = logging.FileHandler(
                os.path.join(log_dir, f"{self.VENDOR_NAME.lower()}_{self.COLLECTOR_TYPE}.log")
            )
            handler.setFormatter(logging.Formatter(
                '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
            ))
            self.logger.addHandler(handler)
            self.logger.setLevel(logging.INFO)
    
    @abstractmethod
    def authenticate(self) -> bool:
        """
        Authenticate with the storage array.
        Returns True if successful, False otherwise.
        """
        pass
    
    @abstractmethod
    def collect(self) -> Dict[str, Any]:
        """
        Perform the data collection.
        Returns dict with collected data.
        """
        pass
    
    @abstractmethod
    def save(self, data: Dict[str, Any]) -> bool:
        """
        Save collected data to the database.
        Returns True if successful, False otherwise.
        """
        pass
    
    def disconnect(self):
        """
        Clean up connections. Override if needed.
        """
        pass
    
    def run(self) -> Dict[str, Any]:
        """
        Execute the full collection cycle.
        Returns stats about the collection.
        """
        self.start_time = datetime.now()
        self.errors = []
        
        result = {
            'array_name': self.array_name,
            'vendor': self.VENDOR_NAME,
            'collector_type': self.COLLECTOR_TYPE,
            'success': False,
            'start_time': self.start_time.isoformat(),
            'end_time': None,
            'duration_seconds': 0,
            'errors': [],
            'stats': {}
        }
        
        try:
            self.logger.info(f"Starting collection for {self.array_name}")
            
            # Step 1: Authenticate
            if not self.authenticate():
                raise Exception("Authentication failed")
            
            # Step 2: Collect data
            data = self.collect()
            if not data:
                raise Exception("No data collected")
            
            # Step 3: Save data
            if not self.save(data):
                raise Exception("Failed to save data")
            
            result['success'] = True
            result['stats'] = self.stats
            self.logger.info(f"Collection completed for {self.array_name}")
            
        except Exception as e:
            error_msg = str(e)
            self.errors.append(error_msg)
            self.logger.error(f"Collection failed for {self.array_name}: {error_msg}")
        
        finally:
            self.disconnect()
            self.end_time = datetime.now()
            result['end_time'] = self.end_time.isoformat()
            result['duration_seconds'] = (self.end_time - self.start_time).total_seconds()
            result['errors'] = self.errors
        
        return result
    
    def log_info(self, message: str):
        """Log an info message"""
        self.logger.info(message)
    
    def log_error(self, message: str):
        """Log an error message"""
        self.logger.error(message)
        self.errors.append(message)
    
    def log_warning(self, message: str):
        """Log a warning message"""
        self.logger.warning(message)


class CollectorRunner:
    """
    Utility class to run collectors for multiple arrays
    """
    
    def __init__(self, collector_class, arrays: List[str]):
        self.collector_class = collector_class
        self.arrays = arrays
        self.results = []
    
    def run_all(self, parallel: bool = False) -> List[Dict[str, Any]]:
        """
        Run collector for all arrays.
        If parallel=True, uses thread pool (future enhancement).
        """
        self.results = []
        
        for array_name in self.arrays:
            collector = self.collector_class(array_name)
            result = collector.run()
            self.results.append(result)
        
        return self.results
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all collection runs"""
        successful = [r for r in self.results if r['success']]
        failed = [r for r in self.results if not r['success']]
        
        return {
            'total': len(self.results),
            'successful': len(successful),
            'failed': len(failed),
            'failed_arrays': [r['array_name'] for r in failed],
            'total_duration': sum(r['duration_seconds'] for r in self.results)
        }
