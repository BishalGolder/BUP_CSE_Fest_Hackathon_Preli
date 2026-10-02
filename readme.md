# GridWise — Energy Optimization API

GridWise is a containerized FastAPI backend for dynamic energy cost optimization. It combines **Large Language Model (LLM) directive parsing** with deterministic **Linear Programming (SciPy HiGHS)** to generate optimized 24-hour battery charge/discharge and grid-import schedules.

The optimizer considers:

- Variable electricity tariffs
- Solar generation forecasts
- Battery capacity and efficiency
- Charging and discharging limits
- Minimum battery reserve requirements
- Natural-language operator constraints

## Key Features

- **LLM Operator Note Parsing** — Extracts quantitative directives from natural-language operator notes, including solar reductions, charging/discharging windows, battery reserve limits, and maximum grid-import thresholds.

- **Deterministic LP Optimization** — Uses SciPy's `linprog` with the **HiGHS** solver to calculate optimal energy dispatch schedules across a 24-hour horizon.

- **Battery State-of-Charge Dynamics** — Models battery capacity limits, charging/discharging efficiency, power constraints, minimum reserve requirements, and end-of-day energy neutrality.

- **Strict Schema Alignment** — Uses fully typed **Pydantic V2** schemas designed to match standard API and evaluator payload specifications.

- **Production-Ready Containerization** — Supports Docker-based deployment with environment-variable configuration.

## Tech Stack

| Component | Technology |
|---|---|
| API Framework | FastAPI |
| ASGI Server | Uvicorn |
| Optimization | SciPy `linprog` / HiGHS |
| Numerical Computing | NumPy |
| Data Validation | Pydantic V2 |
| LLM Provider | Groq API |
| Containerization | Docker |
| Language | Python 3.11+ |

## API Endpoints

### `GET /health`

Returns the current system health status.

#### Response

```json
{
  "status": "ok"
}
```

### `POST /optimize-energy`

Accepts scenario data, solar forecasts, hourly tariffs, battery specifications, and natural-language operator notes, then returns an optimized hourly dispatch plan.

#### Request

```json
{
  "id": "SAMPLE-01",
  "input": {
    "scenario_id": "SAMPLE-01",
    "operator_notes": [
      "Facilities will wash the rooftop solar panels from noon until 2 PM. Usable solar is roughly 25% of forecast."
    ],
    "hours": [
      {
        "hour": 0,
        "demand_kwh": 90.0,
        "solar_kwh": 0.0,
        "tariff_bdt_per_kwh": 6.0
      }
    ],
    "battery": {
      "capacity_kwh": 220.0,
      "initial_energy_kwh": 110.0,
      "minimum_energy_kwh": 40.0,
      "max_charge_kwh_per_hour": 50.0,
      "max_discharge_kwh_per_hour": 50.0,
      "efficiency": 0.95
    }
  }
}
```

> **Note:** The example contains one hourly record for brevity. The optimizer is designed for a 24-hour horizon.

## Getting Started

### Prerequisites

- Python 3.11+
- Docker (optional)
- Groq API key

### Local Installation

Clone the repository:

```bash
git clone <repository-url>
cd gridwise_backend
```

Create a virtual environment.

#### Windows PowerShell

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

#### Linux/macOS

```bash
python -m venv venv
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Environment Configuration

Create a `.env` file in the project root:

```env
GROQ_API_KEY=your_groq_api_key_here
```

> **Important:** Never commit your `.env` file or API keys to source control.

Add the following to `.gitignore`:

```gitignore
.env
venv/
__pycache__/
*.pyc
```

## Running the Application

Start the FastAPI development server:

```bash
uvicorn app.main:app --reload --port 8000
```

The API will be available at:

```text
http://localhost:8000
```

Interactive Swagger documentation:

```text
http://localhost:8000/docs
```

ReDoc documentation:

```text
http://localhost:8000/redoc
```

## Docker Deployment

### Build the Image

```bash
docker build -t gridwise-backend .
```

### Run the Container

```bash
docker run -d \
  -p 8000:8000 \
  --env-file .env \
  --name gridwise-app \
  gridwise-backend
```

The API will then be available at:

```text
http://localhost:8000
```

### View Container Logs

```bash
docker logs gridwise-app
```

### Check Container Status

```bash
docker ps
```

## Testing

Run the automated test suite:

```bash
python test_all_cases.py
```

The test runner validates the optimizer against sample scenarios and edge cases.

You can also test the API interactively through:

```text
http://localhost:8000/docs
```

## Optimization Model

GridWise uses a deterministic Linear Programming model to minimize energy costs while satisfying operational constraints.

The optimizer considers:

- Grid energy imports
- Battery charging
- Battery discharging
- Solar generation
- Hourly electricity tariffs
- Battery state of charge
- Minimum reserve requirements
- Maximum charging/discharging rates
- Operator-defined constraints

The hourly energy balance can be represented conceptually as:

```text
Demand = Solar + Grid Import + Battery Discharge - Battery Charge
```

Battery state of charge is constrained by:

- Maximum battery capacity
- Minimum reserve
- Charging efficiency
- Maximum charging power
- Maximum discharging power
- Initial battery energy
- End-of-day requirements

The resulting schedule provides an hourly dispatch plan that minimizes modeled energy costs while satisfying the supplied constraints.

## Natural-Language Operator Notes

GridWise allows operators to provide instructions using natural language.

For example:

```text
Facilities will wash the rooftop solar panels from noon until 2 PM.
Usable solar is roughly 25% of forecast.
```

The LLM parsing layer converts this instruction into quantitative constraints that can be applied to the optimization model.

Other examples include:

```text
Keep at least 30 kWh in the battery at all times.
```

```text
Do not charge the battery between 6 PM and 9 PM.
```

```text
Grid imports must remain below 100 kWh per hour.
```

## Architecture

```text
                  Operator Notes
                        |
                        v
               +------------------+
               |    LLM Parser    |
               +--------+---------+
                        |
                        v
             Quantitative Constraints
                        |
                        v
 +-------------+  +------------------+
 | Solar       |->|                  |
 | Forecast    |  |                  |
 +-------------+  |                  |
                  |  Linear Program  |
 +-------------+  |     HiGHS        |
 | Demand      |->|                  |
 +-------------+  |                  |
                  |                  |
 +-------------+  |                  |
 | Tariffs     |->|                  |
 +-------------+  +--------+---------+
                           |
 +-------------+           |
 | Battery     |---------->|
 | Specs       |           |
 +-------------+           v
                  Optimized Dispatch
                       Schedule
```

The architecture separates **natural-language interpretation** from **numerical optimization**.

This allows the LLM to interpret operator intent while keeping the final optimization deterministic and reproducible.

## Project Structure

```text
gridwise_backend/
├── app/
│   ├── main.py
│   └── ...
├── test_all_cases.py
├── requirements.txt
├── Dockerfile
├── .env
├── .gitignore
└── README.md
```

## Configuration

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | Yes | API key used for LLM-based operator-note parsing |