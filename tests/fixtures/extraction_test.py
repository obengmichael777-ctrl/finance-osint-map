# tests/fixtures/create_sample_data.py
"""
Generate sample Excel fixtures for testing the extraction pipeline.
Creates files in different formats to simulate heterogeneous store data.
"""

import pandas as pd
from pathlib import Path
import numpy as np
from datetime import datetime, timedelta


def create_store_123_fixtures(output_dir: Path):
    """Create sample data in store_123 format (US store)"""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # Sales data
    dates = pd.date_range('2024-01-01', '2024-01-31', freq='D')
    products = ['SKU-' + str(i).zfill(4) for i in range(100)]
    
    sales_data = []
    for date in dates:
        for _ in range(np.random.randint(20, 50)):
            sales_data.append({
                'Transaction Date': date.strftime('%Y-%m-%d'),
                'SKU': np.random.choice(products),
                'Qty Sold': np.random.randint(1, 100),
                'Net Sales': round(np.random.uniform(10, 1000), 2),
                'Customer ID': f'CUST-{np.random.randint(1000, 9999)}',
                'Payment Type': np.random.choice(
                    ['CASH', 'CREDIT', 'DEBIT', 'MOBILE']
                )
            })
    
    df_sales = pd.DataFrame(sales_data)
    
    # Add some edge cases for testing
    # Row with missing optional field
    edge_case = {
        'Transaction Date': '2024-01-15',
        'SKU': 'SKU-TEST1',
        'Qty Sold': 5,
        'Net Sales': 99.99,
        'Customer ID': np.nan,  # Missing optional field
        'Payment Type': 'CREDIT'
    }
    df_sales = pd.concat([df_sales, pd.DataFrame([edge_case])], ignore_index=True)
    
    # Inventory data
    inventory_data = []
    for product in products[:50]:  # Subset for inventory
        inventory_data.append({
            'SKU': product,
            'Current Stock': np.random.randint(0, 500),
            'Min Stock Level': np.random.randint(10, 50)
        })
    
    df_inventory = pd.DataFrame(inventory_data)
    
    # Write to Excel with formatting
    output_file = output_dir / 'store_123_sales_202401.xlsx'
    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        # Write title rows first
        workbook = writer.book
        sales_sheet = workbook.create_sheet('Sales_2024', 0)
        
        # Add title
        sales_sheet.cell(row=1, column=1, value="Store #123 - Sales Report")
        sales_sheet.cell(row=2, column=1, value="Generated: 2024-02-01")
        
        # Write data starting from row 3
        df_sales.to_excel(writer, sheet_name='Sales_2024', startrow=2, index=False)
        
        # Write inventory
        df_inventory.to_excel(writer, sheet_name='Inventory_Levels', index=False)
    
    return output_file


def create_store_456_fixtures(output_dir: Path):
    """Create sample data in store_456 format (UK store)"""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # UK format with different column names and date format
    dates = pd.date_range('2024-01-01', '2024-01-31', freq='D')
    
    sales_data = []
    for date in dates:
        for _ in range(np.random.randint(15, 40)):
            net_sales = round(np.random.uniform(10, 500), 2)
            vat_rate = 0.20
            vat = round(net_sales * vat_rate, 2)
            
            sales_data.append({
                'Sale_Date': date.strftime('%d/%m/%Y'),  # UK format
                'Product_Code': f'PROD-{np.random.randint(1000, 9999)}',
                'Units_Sold': np.random.randint(1, 50),
                'Gross_Revenue': net_sales,
                'VAT': vat
            })
    
    df_sales = pd.DataFrame(sales_data)
    
    # Add problematic row for testing error handling
    problem_row = {
        'Sale_Date': 'invalid_date',  # Should fail date parsing
        'Product_Code': 'PROD-TEST',
        'Units_Sold': -5,  # Negative quantity
        'Gross_Revenue': 100.00,
        'VAT': 20.00
    }
    df_sales = pd.concat([df_sales, pd.DataFrame([problem_row])], ignore_index=True)
    
    output_file = output_dir / 'store_456_revenue_20240115.xls'
    df_sales.to_excel(output_file, sheet_name='Daily_Sales_2024', index=False)
    
    return output_file


def create_invalid_fixtures(output_dir: Path):
    """Create intentionally invalid files for testing DLQ"""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    # File with missing required columns
    df_missing_cols = pd.DataFrame({
        'Date': ['2024-01-01'],
        'Product': ['TEST'],
        # Missing required 'SKU' and 'Net Sales' columns
        'Amount': [100.00]
    })
    
    output_file = output_dir / 'invalid_missing_columns.xlsx'
    df_missing_cols.to_excel(output_file, sheet_name='Sales_2024', index=False)
    
    # Corrupted file (just write garbage bytes)
    corrupted_file = output_dir / 'corrupted_file.xlsx'
    with open(corrupted_file, 'wb') as f:
        f.write(b'This is not a valid Excel file\x00\x01\x02')
    
    return [output_file, corrupted_file]


def create_all_fixtures():
    """Generate all test fixtures"""
    fixtures_dir = Path(__file__).parent
    
    # Store-specific fixtures
    store_123_dir = fixtures_dir / 'store_123'
    store_456_dir = fixtures_dir / 'store_456'
    invalid_dir = fixtures_dir / 'invalid'
    
    store_123_file = create_store_123_fixtures(store_123_dir)
    print(f"Created store 123 fixture: {store_123_file}")
    
    store_456_file = create_store_456_fixtures(store_456_dir)
    print(f"Created store 456 fixture: {store_456_file}")
    
    invalid_files = create_invalid_fixtures(invalid_dir)
    print(f"Created {len(invalid_files)} invalid fixtures for DLQ testing")
    
    # Create a metadata file with expected schema info
    metadata = {
        'fixtures': {
            'store_123': {
                'file': str(store_123_file.name),
                'expected_sheets': ['Sales_2024', 'Inventory_Levels'],
                'expected_rows': {
                    'Sales_2024': None,  # Variable
                    'Inventory_Levels': 50
                },
                'store_id': 'store_123',
                'country': 'US'
            },
            'store_456': {
                'file': str(store_456_file.name),
                'expected_sheets': ['Daily_Sales_2024'],
                'store_id': 'store_456',
                'country': 'UK',
                'has_errors': True  # Contains problematic rows
            }
        }
    }
    
    import json
    metadata_path = fixtures_dir / 'fixture_metadata.json'
    with open(metadata_path, 'w') as f:
        json.dump(metadata, f, indent=2)
    
    print(f"Created fixture metadata: {metadata_path}")


if __name__ == '__main__':
    create_all_fixtures()