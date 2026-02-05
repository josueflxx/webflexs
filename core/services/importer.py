
import pandas as pd
import openpyxl
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class ImportRowResult:
    """Class to track the result of importing a single row."""
    row_number: int
    data: Dict[str, Any]
    success: bool = False
    errors: List[str] = field(default_factory=list)
    action: str = "skipped"  # created, updated, skipped, error

@dataclass
class ImportResult:
    """Class to aggregate import results."""
    total_rows: int = 0
    created: int = 0
    updated: int = 0
    errors: int = 0
    row_results: List[ImportRowResult] = field(default_factory=list)
    
    @property
    def has_errors(self):
        return self.errors > 0

class BaseImporter(ABC):
    """
    Abstract Base Class for Data Importers.
    Handles file reading, common validation, and result aggregation.
    """
    
    def __init__(self, file):
        self.file = file
        self.results = ImportResult()
        self.df = None
        self.required_columns = []

    def load_data(self):
        """Loads data from Excel file into Pandas DataFrame."""
        try:
            self.df = pd.read_excel(self.file, engine='openpyxl')
            # Normalize Headers: lowercase, strip
            self.df.columns = [str(c).lower().strip() for c in self.df.columns]
            return True
        except Exception as e:
            raise ValueError(f"Error reading file: {str(e)}")

    def validate_structure(self):
        """Checks if required columns exist."""
        if self.df is None:
            raise ValueError("Data not loaded. Call load_data() first.")
        
        missing = [col for col in self.required_columns if col not in self.df.columns]
        if missing:
            raise ValueError(f"Faltan columnas requeridas: {', '.join(missing)}")
        return True

    @abstractmethod
    def process_row(self, row: Dict[str, Any], dry_run: bool = True) -> ImportRowResult:
        """
        Process a single row. 
        Must implement logic for creation/update.
        """
        pass

    def run(self, dry_run: bool = True) -> ImportResult:
        """Executes the import process."""
        self.load_data()
        self.validate_structure()
        
        self.results.total_rows = len(self.df)
        
        for index, row in self.df.iterrows():
            row_dict = row.to_dict()
            # row_number 2 because Excel header is 1, and 0-index
            row_num = index + 2 
            
            try:
                row_result = self.process_row(row_dict, dry_run=dry_run)
                row_result.row_number = row_num
                
                if row_result.success:
                    if row_result.action == 'created':
                        self.results.created += 1
                    elif row_result.action == 'updated':
                        self.results.updated += 1
                else:
                    self.results.errors += 1
                    
                self.results.row_results.append(row_result)
                
            except Exception as e:
                self.results.errors += 1
                self.results.row_results.append(ImportRowResult(
                    row_number=row_num,
                    data=row_dict,
                    success=False,
                    errors=[str(e)],
                    action="error"
                ))
                
        return self.results
