.PHONY: install test data pipeline dashboard api clean

install:
	@echo "Installing dependencies..."
	pip install -r requirements.txt

test:
	@echo "Running tests..."
	pytest tests/ -v --tb=short

data:
	@echo "Generating test data..."
	python tests/generate_test_data_v2.py --stores 3 --rows 200

pipeline:
	@echo "Running ETL pipeline..."
	python test_extraction.py
	python test_transformation.py
	python test_database.py

dashboard:
	@echo "Starting dashboard at http://localhost:8501"
	streamlit run dashboard/app.py --server.port 8501

api:
	@echo "Starting API at http://localhost:8000"
	uvicorn api:app --reload --port 8000

refresh:
	@echo "Running data refresh..."
	bash scripts/refresh_data.sh

clean:
	@echo "Cleaning data directories..."
	rm -rf data/staging/*
	rm -rf data/transformed/*
	rm -rf data/dead_letter_queue/*
	rm -f data/retail.db
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@echo "Done."
