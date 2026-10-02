# GridWise — Energy Optimization API

GridWise is a production-grade, containerized **FastAPI** backend for dynamic 24-hour microgrid energy optimization. It combines **Large Language Model (LLM) directive parsing** with deterministic **Linear Programming (SciPy HiGHS)** to generate optimized battery charge/discharge and grid-import schedules.

The optimizer considers:

- Variable electricity tariffs
- Solar generation forecasts
- Battery capacity and efficiency
- Charging and discharging limits
- Minimum battery reserve requirements
- Natural-language operator constraints
- Solar curtailment directives
- Forced charging and discharging windows
- Maximum grid-import constraints

The system converts unstructured operator instructions into quantitative optimization constraints and passes them into a deterministic mathematical optimization model.

---

## Live Deployment

| Resource | URL |
|---|---|
| Live API | https://gridwise-backend-sass.onrender.com/ |
| Swagger UI | https://gridwise-backend-sass.onrender.com/docs |
| ReDoc | https://gridwise-backend-sass.onrender.com/redoc |
| Health Check | https://gridwise-backend-sass.onrender.com/health |
| OpenAPI Schema | https://gridwise-backend-sass.onrender.com/openapi.json |

> **Deployment Note:** The application is deployed on Render's free tier. The web service may enter sleep mode after 15 minutes of inactivity. Initial requests after inactivity may take approximately 20–30 seconds while the container wakes up.

---

## Key Features

- **LLM Operator Note Parsing** — Extracts quantitative directives from natural-language operator notes, including solar reductions, charging/discharging windows, battery reserve limits, and maximum grid-import thresholds.
- **Deterministic LP Optimization** — Uses SciPy's `linprog` with the **HiGHS** solver to calculate optimized energy dispatch schedules across a 24-hour horizon.
- **Battery State-of-Charge Dynamics** — Models battery capacity limits, charging/discharging efficiency, power constraints, minimum reserve requirements, and end-of-day energy neutrality.
- **Solar Curtailment Handling** — Converts natural-language solar availability instructions into hourly solar-generation constraints.
- **Dynamic Reserve Constraints** — Supports operator-defined battery reserve requirements and converts applicable percentage-based instructions into quantitative energy constraints.
- **Strict Schema Alignment** — Uses fully typed **Pydantic V2** schemas for structured API requests and responses.
- **Production-Ready Containerization** — Supports Docker-based deployment with environment-variable configuration.
- **Automated Testing** — Includes end-to-end and edge-case test suites for validating the optimization engine.

---

## Tech Stack

| Component | Technology |
|---|---|
| API Framework | FastAPI |
| ASGI Server | Uvicorn |
| Optimization | SciPy `linprog` / HiGHS |
| Numerical Computing | NumPy |
| Data Validation | Pydantic V2 |
| LLM Provider | Groq API |
| LLM Models | `llama-3.3-70b-versatile`, `llama3-8b-8192` |
| Containerization | Docker |
| Cloud Hosting | Render |
| Language | Python 3.11+ |

---

## API Endpoints

### `GET /`

Returns basic API operational metadata.

### `GET /health`

Returns the current system health status.

**Response**

```json
{
  "status": "ok"
}
```

### `POST /optimize-energy`

Accepts scenario data, solar forecasts, hourly tariffs, battery specifications, and natural-language operator notes, then returns an optimized hourly dispatch plan.

**Request**

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

> **Note:** The example above contains a single hourly record for brevity. The optimizer is designed for a 24-hour horizon.

**Response**

```json
{
  "scenario_id": "SAMPLE-01",
  "status": "SUCCESS",
  "interpretation": {
    "solar_reduction_percent": 75.0,
    "solar_reduction_hours": [12, 13],
    "forced_charging_hours": [],
    "forced_discharging_hours": [],
    "min_battery_reserve_kwh": 40.0,
    "max_grid_import_kwh": null,
    "notes_summary": "Parsed panel washing directive for hours 12-13."
  },
  "summary": {
    "total_cost_bdt": 12450.50,
    "total_grid_import_kwh": 1420.0,
    "total_solar_used_kwh": 310.0,
    "total_battery_charged_kwh": 180.0,
    "total_battery_discharged_kwh": 171.0
  },
  "hourly_plan": [
    {
      "hour": 0,
      "grid_import_kwh": 90.0,
      "solar_used_kwh": 0.0,
      "battery_charge_kwh": 0.0,
      "battery_discharge_kwh": 0.0,
      "battery_soc_kwh": 110.0,
      "cost_bdt": 540.0
    }
  ]
}
```

---

## Getting Started

### Prerequisites

- Python 3.11+
- Git
- Docker (optional)
- Groq API key

### Local Installation

1. **Clone the repository:**

   ```bash
   git clone <repository-url>
   cd gridwise_backend
   ```

2. **Create a virtual environment.**

   **Windows PowerShell**

   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```

   **Linux / macOS**

   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```

3. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

### Environment Configuration

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

### Running the Application

Start the FastAPI development server:

```bash
uvicorn app.main:app --reload --port 8000
```

The API will be available at:

- **API root:** http://localhost:8000
- **Swagger UI:** http://localhost:8000/docs
- **ReDoc:** http://localhost:8000/redoc
- **OpenAPI spec:** http://localhost:8000/openapi.json

---

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

The API will then be available at http://localhost:8000

### View Container Logs

```bash
docker logs gridwise-app
```

### Check Container Status

```bash
docker ps
```

---

## Testing

GridWise includes automated tests for validating the optimization engine against normal scenarios and complex edge cases.

Available test files include:

- `test_all_cases.py`
- `test_edge_cases.py`

The test suite covers:

- High- and low-tariff optimization windows
- Solar curtailment directives
- Distractor notes in operator instructions
- Dynamic percentage-to-kWh battery reserve conversion
- Forced battery charging
- Forced battery discharging
- Battery capacity limits
- Charge and discharge power limits
- Zero-efficiency battery scenarios
- Grid import constraints
- End-of-day battery requirements

Run the main test suite:

```bash
python test_all_cases.py
```

The API can also be tested interactively through http://localhost:8000/docs

---

## Optimization Model

GridWise uses a deterministic Linear Programming model to minimize total grid electricity acquisition cost while satisfying physical and operational constraints.

The optimization considers:

- Grid energy imports
- Battery charging
- Battery discharging
- Solar generation
- Hourly electricity tariffs
- Battery state of charge
- Minimum reserve requirements
- Maximum charging rates
- Maximum discharging rates
- Solar availability reductions
- Forced operating windows
- Maximum grid-import limits
- End-of-day battery requirements

### Objective Function

The optimization minimizes the total cost of electricity imported from the grid:

```text
minimize  Σ(t = 0 → 23)  Tariff_t · P_grid,t
```

### Power Balance

For each hour, energy supply must satisfy demand and battery charging:

```text
P_grid,t + P_solar,t + P_discharge,t = P_demand,t + P_charge,t
```

Conceptually:

```text
Demand = Solar + Grid Import + Battery Discharge − Battery Charge
```

### Battery State of Charge

Battery energy evolves according to charging and discharging efficiency:

```text
E_(t+1) = E_t + (η_charge · P_charge,t) − (1 / η_discharge · P_discharge,t)
```

### Battery Bounds

The optimizer enforces:

```text
E_min,t ≤ E_t ≤ E_max
```

Charging is constrained by:

```text
0 ≤ P_charge,t ≤ P_charge,max
```

Discharging is constrained by:

```text
0 ≤ P_discharge,t ≤ P_discharge,max
```

### End-of-Day Requirement

The final battery energy must satisfy:

```text
E_24 ≥ E_initial
```

This prevents the optimizer from reducing the modeled cost by simply ending the optimization horizon with less stored energy than it started with.

---

## Natural-Language Operator Notes

GridWise allows operators to provide operational instructions using natural language.

For example:

> Facilities will wash the rooftop solar panels from noon until 2 PM.
> Usable solar is roughly 25% of forecast.

The LLM parsing layer converts this instruction into quantitative constraints such as:

- **Solar reduction:** 75%
- **Affected hours:** 12, 13

Other examples include:

> Keep at least 30 kWh in the battery at all times.

This becomes a **minimum state-of-charge constraint**.

> Do not charge the battery between 6 PM and 9 PM.

This becomes a **charging constraint** over the affected hours.

> Grid imports must remain below 100 kWh per hour.

This becomes a **maximum grid-import constraint**.

The LLM is responsible for interpreting the operator's natural-language intent. The resulting quantitative directives are then applied by the deterministic optimization engine.

---

## Architecture

```text
                         Operator Notes
                              |
                              v
                     +------------------+
                     |    LLM Parser    |
                     |    Groq API      |
                     +--------+---------+
                              |
                              v
                  Quantitative Directives
                              |
                              v
                     +------------------+
                     | Constraint       |
                     | Normalization    |
                     +--------+---------+
                              |
                              v
 +-------------+       +------------------+
 | Solar       |------>|                  |
 | Forecast    |       |                  |
 +-------------+       |                  |
                       |  Linear Program  |
 +-------------+       |     HiGHS        |
 | Demand      |------>|                  |
 +-------------+       |                  |
                       |                  |
 +-------------+       |                  |
 | Tariffs     |------>|                  |
 +-------------+       +--------+---------+
                                |
 +-------------+                |
 | Battery     |----------------+
 | Specs       |
 +-------------+                |
                                v
                       Optimized Dispatch
                            Schedule
                                |
                                v
                       Cost & Energy Metrics
```

The architecture separates natural-language interpretation from numerical optimization.

This allows the LLM to interpret operator intent while keeping the final optimization deterministic, reproducible, and solver-driven.

---

## Data Flow

The optimization pipeline follows these stages:

1. **Request Validation** — Pydantic V2 validates the incoming scenario, hourly demand, solar forecasts, tariffs, battery specifications, and operator notes.
2. **Natural-Language Parsing** — The Groq LLM analyzes the operator notes and extracts relevant quantitative directives.
3. **Constraint Normalization** — Parsed directives are converted into normalized values suitable for the optimization model.
4. **Linear Programming Formulation** — The optimizer creates decision variables and constraints for the 24-hour planning horizon.
5. **HiGHS Optimization** — SciPy's HiGHS solver calculates the minimum-cost feasible dispatch schedule.
6. **Result Generation** — The API returns the interpreted directives, aggregate optimization metrics, and hourly dispatch schedule.

---

## Key Design Principles

### Natural Language to Deterministic Optimization

GridWise uses the LLM primarily as a **directive-extraction layer**.

The LLM does not directly decide the final energy schedule. Instead, it converts natural-language instructions into structured quantitative constraints.

The deterministic optimization engine then uses those constraints to calculate the final dispatch schedule.

### Constraint-Driven Scheduling

Operator instructions can modify the optimization problem through constraints such as:

- Solar availability reductions
- Battery reserve requirements
- Grid import limits
- Forced charging
- Forced discharging
- Time-specific operating restrictions

### Physical Battery Modeling

Battery efficiency, energy capacity, charge limits, discharge limits, initial energy, and state-of-charge dynamics are explicitly represented in the optimization model.

### Deterministic Solver

Once the operator directives have been converted into quantitative constraints, the optimization stage is deterministic and solver-driven.

This separation improves reproducibility and makes the numerical optimization independent of the LLM's free-form generation.

### Containerized Deployment

The backend is packaged as a Docker image, allowing the same application to run consistently in local development and cloud environments.

---

## API Documentation

FastAPI automatically generates interactive API documentation.

### Swagger UI — `/docs`

- Local: http://localhost:8000/docs
- Production: https://gridwise-backend-sass.onrender.com/docs

### OpenAPI Specification — `/openapi.json`

- Local: http://localhost:8000/openapi.json
- Production: https://gridwise-backend-sass.onrender.com/openapi.json

### ReDoc — `/redoc`

- Local: http://localhost:8000/redoc
- Production: https://gridwise-backend-sass.onrender.com/redoc

---

## Project Structure

```text
gridwise_backend/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI application and endpoint routing
│   ├── schemas.py           # Pydantic V2 input/output models
│   ├── llm.py               # Groq LLM operator-note parsing
│   └── optimizer.py         # SciPy HiGHS optimization engine
│
├── Dockerfile               # Production container definition
├── .dockerignore            # Docker build exclusions
├── .gitignore               # Secrets and artifact exclusions
├── requirements.txt         # Python dependencies
├── test_all_cases.py        # Comprehensive benchmark test suite
├── test_edge_cases.py       # Edge-case validation tests
└── README.md                # Project documentation
```

---

## Configuration

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | Yes | API key used for LLM-based operator-note parsing |
```