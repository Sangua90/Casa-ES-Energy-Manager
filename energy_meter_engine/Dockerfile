FROM python:3.13.7-alpine3.22
RUN apk add --no-cache tzdata && adduser -D engine
WORKDIR /app
COPY engine.py server.py house_climate_plan.py thermal_model.py horizon.py ./
ENV PYTHONUNBUFFERED=1
EXPOSE 8099
CMD ["python", "server.py"]
