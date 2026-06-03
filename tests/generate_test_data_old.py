# generate_test_data.py
import pandas as pd
import numpy as np
from faker import Faker
from datetime import datetime, timedelta
import random
import os
from openpyxl import Workbook

fake = Faker()

class StoreDataGenerator:
    """Generate realistic Excel files with varying formats"""
    
    def __init__(self, output_dir="tests/fixtures/excel_samples"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        
    def create_american_store_format(self, store_id, num_rows=100):
        """US store format - typical retail POS export"""
        dates = [fake.date_between(start_date='-1y', end_date='today') for _ in range(num_rows)]
        data = {
            'Transaction Date': dates,
            'SKU': [fake.ean13() for _ in range(num_rows)],
            'Product Description': [fake.catch_phrase() for _ in range(num_rows)],
            'Quantity': [random.randint(1, 10) for _ in range(num_rows)],
            'Unit Price': [round(random.uniform(5, 200), 2) for _ in range(num_rows)],
            'Net Sales': [round(random.uniform(5, 2000), 2) for _ in range(num_rows)],
            'Store Location': [fake.city() for _ in range(num_rows)],
            'Cashier ID': [fake.random_number(digits=4) for _ in range(num_rows)]
        }
        df = pd.DataFrame(data)
        # Multiple sheets to simulate complexity
        with pd.ExcelWriter(f"{self.output_dir}/store_{store_id}_US.xlsx") as writer:
            df.to_excel(writer, sheet_name="Sales_2024", index=False)
            df.groupby('Store Location').size().to_excel(writer, sheet_name="Summary_by_Store")
        return f"store_{store_id}_US.xlsx"
    
    def create_european_store_format(self, store_id, num_rows=100):
        """European format - different column naming conventions"""
        dates = [fake.date_between(start_date='-1y', end_date='today') for _ in range(num_rows)]
        data = {
            'Date de Transaction': dates,  # French column names
            'Code Article': [fake.ean8() for _ in range(num_rows)],
            'Description': [fake.sentence(nb_words=3) for _ in range(num_rows)],
            'Quantité': [random.randint(1, 10) for _ in range(num_rows)],
            'Prix Unitaire': [round(random.uniform(5, 200), 2) for _ in range(num_rows)],
            'Chiffre d\'Affaires': [round(random.uniform(5, 2000), 2) for _ in range(num_rows)],
            'Magasin': [fake.city() for _ in range(num_rows)]
        }
        df = pd.DataFrame(data)
        df.to_excel(f"{self.output_dir}/store_{store_id}_EU.xlsx", sheet_name="Sales", index=False)
        return f"store_{store_id}_EU.xlsx"
    
    def create_asia_store_format(self, store_id, num_rows=100):
        """Asian format - different structure entirely"""
        dates = [fake.date_between(start_date='-1y', end_date='today') for _ in range(num_rows)]
        data = {
            '日付': dates,  # Japanese for "Date"
            '商品コード': [fake.bothify(text='??###') for _ in range(num_rows)],
            '商品名': [fake.word() + ' ' + fake.word() for _ in range(num_rows)],
            '数量': [random.randint(1, 10) for _ in range(num_rows)],
            '単価': [round(random.uniform(5, 200), 2) for _ in range(num_rows)],
            '売上高': [round(random.uniform(5, 2000), 2) for _ in range(num_rows)]
        }
        df = pd.DataFrame(data)
        # Multiple sheets - different structure
        with pd.ExcelWriter(f"{self.output_dir}/store_{store_id}_ASIA.xlsx") as writer:
            df.to_excel(writer, sheet_name="Raw_Sales", index=False)
            df.groupby('商品コード')['売上高'].sum().to_excel(writer, sheet_name="Product_Summary")
        return f"store_{store_id}_ASIA.xlsx"
    
    def generate_all_test_files(self):
        """Generate 15+ varied files for testing"""
        files = []
        # 5 US stores
        for i in range(1, 6):
            files.append(self.create_american_store_format(i, random.randint(50, 200)))
        # 5 EU stores  
        for i in range(6, 11):
            files.append(self.create_european_store_format(i, random.randint(50, 200)))
        # 5 Asia stores
        for i in range(11, 16):
            files.append(self.create_asia_store_format(i, random.randint(50, 200)))
        
        print(f"✅ Generated {len(files)} test Excel files in {self.output_dir}")
        return files

if __name__ == "__main__":
    generator = StoreDataGenerator()
    generator.generate_all_test_files()