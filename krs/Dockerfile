# Use official Python image as base
FROM python:3.11

# Set working directory inside the container
WORKDIR /app

# Copy only requirements first (to leverage Docker cache)
COPY requirements.txt /app/

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application files
COPY . /app/

# Expose a port (if applicable, e.g., for FastAPI or Flask)
EXPOSE 8000

# Default command to run your application
CMD ["python", "main.py"]
