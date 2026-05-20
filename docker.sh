#!/usr/bin/env bash
set -euo pipefail

CMD=${1:-help}

case "$CMD" in
  build)
    docker compose build
    ;;
  up)
    docker compose up -d --build
    ;;
  start)
    docker compose up --build
    ;;
  down)
    docker compose down
    ;;
  logs)
    docker compose logs -f
    ;;
  restart)
    docker compose down && docker compose up -d --build
    ;;
  status)
    docker compose ps
    ;;
  *)
    echo "Usage: $0 {build|up|start|down|logs|restart|status}"
    echo ""
    echo "  build    Rebuild image without starting"
    echo "  up       Build and start in background"
    echo "  start    Build and start in foreground (logs to terminal)"
    echo "  down     Stop and remove containers"
    echo "  logs     Tail container logs"
    echo "  restart  Full stop, rebuild, and start in background"
    echo "  status   Show container status"
    ;;
esac
