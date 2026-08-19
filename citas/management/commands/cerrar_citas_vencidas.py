from django.core.management.base import BaseCommand

from citas.services import cerrar_citas_vencidas


class Command(BaseCommand):
    help = (
        'Marca como "no atendida" toda cita confirmada cuya hora de fin ya '
        'pasó en hora local (HU-17). No libera el slot: la cita queda '
        'registrada como un hecho, no como cancelada. Pensado para '
        'ejecutarse periódicamente; esta fase no automatiza esa ejecución.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--simular', action='store_true',
            help='No escribe cambios: solo informa cuántas citas se cerrarían.',
        )

    def handle(self, *args, **options):
        simular = options['simular']
        total = cerrar_citas_vencidas(dry_run=simular)
        if simular:
            self.stdout.write(self.style.WARNING(
                f'[Simulación] {total} cita(s) se marcarían como no atendida.',
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'{total} cita(s) marcada(s) como no atendida.',
            ))
