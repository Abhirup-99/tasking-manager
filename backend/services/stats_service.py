from cachetools import TTLCache, cached

from sqlalchemy import func, desc, distinct, cast, extract, or_, and_, tuple_
from sqlalchemy.sql.functions import coalesce
from sqlalchemy.types import Time
from backend import db
from backend.models.dtos.stats_dto import (
    ProjectContributionsDTO,
    UserContribution,
    Pagination,
    TaskHistoryDTO,
    TaskStatusDTO,
    ProjectActivityDTO,
    ProjectLastActivityDTO,
    HomePageStatsDTO,
    OrganizationListStatsDTO,
    CampaignStatsDTO,
    TaskStats,
    TaskStatsDTO,
)

from backend.models.dtos.project_dto import ProjectSearchResultsDTO
from backend.models.postgis.campaign import Campaign, campaign_projects
from backend.models.postgis.organisation import Organisation
from backend.models.postgis.project import Project
from backend.models.postgis.statuses import TaskStatus, MappingLevel
from backend.models.postgis.task import TaskHistory, User, Task, TaskAction
from backend.models.postgis.utils import timestamp, NotFound  # noqa: F401
from backend.services.project_service import ProjectService
from backend.services.project_search_service import ProjectSearchService
from backend.services.users.user_service import UserService
from backend.services.organisation_service import OrganisationService
from backend.services.campaign_service import CampaignService

from datetime import date, timedelta

homepage_stats_cache = TTLCache(maxsize=4, ttl=30)


class StatsService:
    @staticmethod
    def update_stats_after_task_state_change(
        project_id: int,
        user_id: int,
        last_state: TaskStatus,
        new_state: TaskStatus,
        action="change",
    ):
        """ Update stats when a task has had a state change """

        if new_state in [
            TaskStatus.LOCKED_FOR_VALIDATION,
            TaskStatus.LOCKED_FOR_MAPPING,
        ]:
            return  # No stats to record for these states

        project = ProjectService.get_project_by_id(project_id)
        user = UserService.get_user_by_id(user_id)

        project, user = StatsService._update_tasks_stats(
            project, user, last_state, new_state, action
        )
        UserService.upsert_mapped_projects(user_id, project_id)
        project.last_updated = timestamp()

        # Transaction will be saved when task is saved
        return project, user

    @staticmethod
    def _update_tasks_stats(
        project: Project,
        user: User,
        last_state: TaskStatus,
        new_state: TaskStatus,
        action="change",
    ):

        # Make sure you are aware that users table has it as incrementing counters,
        # while projects table reflect the actual state, and both increment and decrement happens

        if new_state == last_state:
            return project, user

        # Set counters for new state
        if new_state == TaskStatus.MAPPED:
            project.tasks_mapped += 1
        elif new_state == TaskStatus.VALIDATED:
            project.tasks_validated += 1
        elif new_state == TaskStatus.BADIMAGERY:
            project.tasks_bad_imagery += 1

        if action == "change":
            if new_state == TaskStatus.MAPPED:
                user.tasks_mapped += 1
            elif new_state == TaskStatus.VALIDATED:
                user.tasks_validated += 1
            elif new_state == TaskStatus.INVALIDATED:
                user.tasks_invalidated += 1

        # Remove counters for old state
        if last_state == TaskStatus.MAPPED:
            project.tasks_mapped -= 1
        elif last_state == TaskStatus.VALIDATED:
            project.tasks_validated -= 1
        elif last_state == TaskStatus.BADIMAGERY:
            project.tasks_bad_imagery -= 1

        if action == "undo":
            if last_state == TaskStatus.MAPPED:
                user.tasks_mapped -= 1
            elif last_state == TaskStatus.VALIDATED:
                user.tasks_validated -= 1
            elif last_state == TaskStatus.INVALIDATED:
                user.tasks_invalidated -= 1

        return project, user

    @staticmethod
    def get_latest_activity(project_id: int, page: int) -> ProjectActivityDTO:
        """ Gets all the activity on a project """

        if not ProjectService.exists(project_id):
            raise NotFound

        results = (
            db.session.query(
                TaskHistory.id,
                TaskHistory.task_id,
                TaskHistory.action,
                TaskHistory.action_date,
                TaskHistory.action_text,
                User.username,
            )
            .join(User)
            .filter(
                TaskHistory.project_id == project_id,
                TaskHistory.action != TaskAction.COMMENT.name,
            )
            .order_by(TaskHistory.action_date.desc())
            .paginate(page, 10, True)
        )

        activity_dto = ProjectActivityDTO()
        for item in results.items:
            history = TaskHistoryDTO()
            history.task_id = item.id
            history.task_id = item.task_id
            history.action = item.action
            history.action_text = item.action_text
            history.action_date = item.action_date
            history.action_by = item.username
            activity_dto.activity.append(history)

        activity_dto.pagination = Pagination(results)
        return activity_dto

    @staticmethod
    def get_popular_projects() -> ProjectSearchResultsDTO:
        """ Get all projects ordered by task_history """

        rate_func = func.count(TaskHistory.user_id) / extract(
            "epoch", func.sum(cast(TaskHistory.action_date, Time))
        )

        query = (
            TaskHistory.query.with_entities(
                TaskHistory.project_id.label("id"), rate_func.label("rate")
            )
            .filter(TaskHistory.action_date >= date.today() - timedelta(days=90))
            .filter(
                or_(
                    TaskHistory.action == TaskAction.LOCKED_FOR_MAPPING.name,
                    TaskHistory.action == TaskAction.LOCKED_FOR_VALIDATION.name,
                )
            )
            .filter(TaskHistory.action_text is not None)
            .filter(TaskHistory.action_text != "")
            .group_by(TaskHistory.project_id)
            .order_by(desc("rate"))
            .limit(10)
            .subquery()
        )

        projects_query = ProjectSearchService.create_search_query()
        projects = projects_query.filter(Project.id == query.c.id).all()
        # Get total contributors.
        contrib_counts = ProjectSearchService.get_total_contributions(projects)
        zip_items = zip(projects, contrib_counts)

        dto = ProjectSearchResultsDTO()
        dto.results = [
            ProjectSearchService.create_result_dto(p, "en", t) for p, t in zip_items
        ]

        return dto

    @staticmethod
    def get_last_activity(project_id: int) -> ProjectLastActivityDTO:
        """ Gets the last activity for a project's tasks """
        sq = (
            TaskHistory.query.with_entities(
                TaskHistory.task_id,
                TaskHistory.action_date,
                TaskHistory.user_id,
            )
            .filter(TaskHistory.project_id == project_id)
            .filter(TaskHistory.action != TaskAction.COMMENT.name)
            .order_by(TaskHistory.task_id, TaskHistory.action_date.desc())
            .distinct(TaskHistory.task_id)
            .subquery()
        )

        sq_statuses = (
            Task.query.with_entities(Task.id, Task.task_status)
            .filter(Task.project_id == project_id)
            .subquery()
        )
        results = (
            db.session.query(
                sq_statuses.c.id,
                sq.c.action_date,
                sq_statuses.c.task_status,
                User.username,
            )
            .outerjoin(sq, sq.c.task_id == sq_statuses.c.id)
            .outerjoin(User, User.id == sq.c.user_id)
            .order_by(sq_statuses.c.id)
            .all()
        )

        dto = ProjectLastActivityDTO()
        dto.activity = [
            TaskStatusDTO(
                dict(
                    task_id=r.id,
                    task_status=TaskStatus(r.task_status).name,
                    action_date=r.action_date,
                    action_by=r.username,
                )
            )
            for r in results
        ]

        return dto

    @staticmethod
    def get_user_contributions(project_id: int) -> ProjectContributionsDTO:
        """ Get all user contributions on a project"""

        mapped_stmt = (
            Task.query.with_entities(
                Task.mapped_by,
                func.count(Task.mapped_by).label("count"),
                func.array_agg(Task.id).label("task_ids"),
            )
            .filter(Task.project_id == project_id)
            .group_by(Task.mapped_by)
            .subquery()
        )
        validated_stmt = (
            Task.query.with_entities(
                Task.validated_by,
                func.count(Task.validated_by).label("count"),
                func.array_agg(Task.id).label("task_ids"),
            )
            .filter(Task.project_id == project_id)
            .group_by(Task.validated_by)
            .subquery()
        )

        results = (
            db.session.query(
                User.id,
                User.username,
                User.name,
                User.mapping_level,
                User.picture_url,
                User.date_registered,
                coalesce(mapped_stmt.c.count, 0).label("mapped"),
                coalesce(validated_stmt.c.count, 0).label("validated"),
                (
                    coalesce(mapped_stmt.c.count, 0)
                    + coalesce(validated_stmt.c.count, 0)
                ).label("total"),
                mapped_stmt.c.task_ids.label("mapped_tasks"),
                validated_stmt.c.task_ids.label("validated_tasks"),
            )
            .outerjoin(
                validated_stmt,
                mapped_stmt.c.mapped_by == validated_stmt.c.validated_by,
                full=True,
            )
            .join(
                User,
                or_(
                    User.id == mapped_stmt.c.mapped_by,
                    User.id == validated_stmt.c.validated_by,
                ),
            )
            .order_by(desc("total"))
            .all()
        )

        contrib_dto = ProjectContributionsDTO()
        user_contributions = [
            UserContribution(
                dict(
                    username=r.username,
                    name=r.name,
                    mapping_level=MappingLevel(r.mapping_level).name,
                    picture_url=r.picture_url,
                    mapped=r.mapped,
                    validated=r.validated,
                    total=r.total,
                    mapped_tasks=r.mapped_tasks if r.mapped_tasks is not None else [],
                    validated_tasks=r.validated_tasks
                    if r.validated_tasks is not None
                    else [],
                    date_registered=r.date_registered.date(),
                )
            )
            for r in results
        ]
        contrib_dto.user_contributions = user_contributions

        return contrib_dto

    @staticmethod
    @cached(homepage_stats_cache)
    def get_homepage_stats(abbrev=True) -> HomePageStatsDTO:
        """ Get overall TM stats to give community a feel for progress that's being made """
        dto = HomePageStatsDTO()
        dto.total_projects = Project.query.with_entities(
            func.count(Project.id)
        ).scalar()
        dto.mappers_online = (
            Task.query.with_entities(func.count(Task.locked_by.distinct()))
            .filter(Task.locked_by.isnot(None))
            .scalar()
        )
        dto.total_mappers = User.query.with_entities(func.count(User.id)).scalar()
        dto.tasks_mapped = (
            Task.query.with_entities(func.count())
            .filter(
                Task.task_status.in_(
                    (TaskStatus.MAPPED.value, TaskStatus.VALIDATED.value)
                )
            )
            .scalar()
        )
        if not abbrev:
            dto.total_validators = (
                Task.query.filter(Task.task_status == TaskStatus.VALIDATED.value)
                .distinct(Task.validated_by)
                .count()
            )
            dto.tasks_validated = Task.query.filter(
                Task.task_status == TaskStatus.VALIDATED.value
            ).count()

            dto.total_area = Project.query.with_entities(
                func.coalesce(func.sum(func.ST_Area(Project.geometry, True) / 1000000))
            ).scalar()

            dto.total_mapped_area = (
                Task.query.with_entities(
                    func.coalesce(func.sum(func.ST_Area(Task.geometry, True) / 1000000))
                )
                .filter(Task.task_status == TaskStatus.MAPPED.value)
                .scalar()
            )

            dto.total_validated_area = (
                Task.query.with_entities(
                    func.coalesce(func.sum(func.ST_Area(Task.geometry, True) / 1000000))
                )
                .filter(Task.task_status == TaskStatus.VALIDATED.value)
                .scalar()
            )

            unique_campaigns = Campaign.query.with_entities(
                func.count(Campaign.id)
            ).scalar()

            linked_campaigns_count = (
                Campaign.query.join(
                    campaign_projects, Campaign.id == campaign_projects.c.campaign_id
                )
                .with_entities(
                    Campaign.name, func.count(campaign_projects.c.campaign_id)
                )
                .group_by(Campaign.id)
                .all()
            )

            subquery = (
                db.session.query(campaign_projects.c.project_id.distinct())
                .order_by(campaign_projects.c.project_id)
                .subquery()
            )
            no_campaign_count = (
                Project.query.with_entities(func.count())
                .filter(~Project.id.in_(subquery))
                .scalar()
            )
            dto.campaigns = [CampaignStatsDTO(row) for row in linked_campaigns_count]
            if no_campaign_count:
                dto.campaigns.append(
                    CampaignStatsDTO(("Unassociated", no_campaign_count))
                )

            dto.total_campaigns = unique_campaigns
            unique_orgs = Organisation.query.with_entities(
                func.count(Organisation.id)
            ).scalar()

            linked_orgs_count = (
                db.session.query(Organisation.name, func.count(Project.organisation_id))
                .join(Project.organisation)
                .group_by(Organisation.id)
                .all()
            )

            subquery = (
                db.session.query(Project.organisation_id.distinct())
                .order_by(Project.organisation_id)
                .subquery()
            )
            no_org_project_count = (
                Organisation.query.with_entities(func.count())
                .filter(~Organisation.id.in_(subquery))
                .scalar()
            )
            dto.organisations = [
                OrganizationListStatsDTO(row) for row in linked_orgs_count
            ]

            if no_org_project_count:
                no_org_proj = OrganizationListStatsDTO(
                    ("Unassociated", no_org_project_count)
                )
                dto.organisations.append(no_org_proj)

            dto.total_organisations = unique_orgs
        else:
            # Clear null attributes for abbreviated call
            clear_attrs = [
                "total_validators",
                "tasks_validated",
                "total_area",
                "total_mapped_area",
                "total_validated_area",
                "campaigns",
                "total_campaigns",
                "organisations",
                "total_organisations",
            ]

            for attr in clear_attrs:
                delattr(dto, attr)

        return dto

    @staticmethod
    def update_all_project_stats():
        projects = db.session.query(Project.id)
        for project_id in projects.all():
            StatsService.update_project_stats(project_id)

    @staticmethod
    def update_project_stats(project_id: int):
        project = ProjectService.get_project_by_id(project_id)
        tasks = Task.query.filter(Task.project_id == project_id)

        project.total_tasks = tasks.count()
        project.tasks_mapped = tasks.filter(
            Task.task_status == TaskStatus.MAPPED.value
        ).count()
        project.tasks_validated = tasks.filter(
            Task.task_status == TaskStatus.VALIDATED.value
        ).count()
        project.tasks_bad_imagery = tasks.filter(
            Task.task_status == TaskStatus.BADIMAGERY.value
        ).count()
        project.save()

    @staticmethod
    def set_task_stats(date):
        date_dto = TaskStats(
            {
                "date": date,
                "mapped": 0,
                "validated": 0,
                "bad_imagery": 0,
            }
        )
        return date_dto

    @staticmethod
    def get_task_stats(
        start_date, end_date, org_id, org_name, campaign, project_id, country
    ):
        """ Creates tasks stats for a period using the TaskStatsDTO """

        query = (
            db.session.query(
                TaskHistory.task_id,
                TaskHistory.project_id,
                TaskHistory.action_text,
                func.DATE(TaskHistory.action_date).label("day"),
            )
            .filter(
                TaskHistory.action == "STATE_CHANGE",
                or_(
                    TaskHistory.action_text == "MAPPED",
                    TaskHistory.action_text == "VALIDATED",
                    TaskHistory.action_text == "BADIMAGERY",
                ),
            )
            .filter(
                and_(
                    func.DATE(TaskHistory.action_date) >= start_date,
                    func.DATE(TaskHistory.action_date) <= end_date,
                )
            )
            .group_by(
                TaskHistory.action_text,
                "day",
                TaskHistory.project_id,
                TaskHistory.task_id,
            )
            .order_by("day")
        )

        if org_id:
            query = query.join(Project, Project.id == TaskHistory.project_id).filter(
                Project.organisation_id == org_id
            )
        if org_name:
            try:
                organisation_id = OrganisationService.get_organisation_by_name(
                    org_name
                ).id
            except NotFound:
                organisation_id = None
            query = query.join(Project, Project.id == TaskHistory.project_id).filter(
                Project.organisation_id == organisation_id
            )
        if campaign:
            try:
                campaign_id = CampaignService.get_campaign_by_name(campaign).id
            except NotFound:
                campaign_id = None
            query = query.join(
                campaign_projects,
                campaign_projects.c.project_id == TaskHistory.project_id,
            ).filter(campaign_projects.c.campaign_id == campaign_id)
        if project_id:
            query = query.filter(TaskHistory.project_id.in_(project_id))
        if country:
            # Unnest country column array.
            sq = Project.query.with_entities(
                Project.id, func.unnest(Project.country).label("country")
            ).subquery()

            query = query.filter(sq.c.country.ilike("%{}%".format(country))).filter(
                TaskHistory.project_id == sq.c.id
            )

        query = query.subquery()
        mapped_query = (
            db.session.query(
                query.c.day.label("day"),
                tuple_(query.c.task_id, query.c.project_id).label("task_project"),
            )
            .select_from(query)
            .distinct(tuple_(query.c.task_id, query.c.project_id))
            .filter(query.c.action_text == "MAPPED")
            .group_by(query.c.task_id, query.c.project_id, query.c.day)
            .order_by(query.c.task_id, query.c.project_id, query.c.day)
            .subquery()
        )
        tasks_mapped_q = db.session.query(
            func.to_char(mapped_query.c.day, "YYYY-MM-DD"),
            func.count(mapped_query.c.task_project),
        ).group_by(mapped_query.c.day)
        tasks_mapped = dict(tasks_mapped_q.all())

        validated_query = (
            db.session.query(
                query.c.day.label("day"),
                tuple_(query.c.task_id, query.c.project_id).label("task_project"),
            )
            .select_from(query)
            .distinct(tuple_(query.c.task_id, query.c.project_id))
            .filter(query.c.action_text == "VALIDATED")
            .group_by(query.c.task_id, query.c.project_id, query.c.day)
            .order_by(query.c.task_id, query.c.project_id, query.c.day)
            .subquery()
        )
        tasks_validated_q = db.session.query(
            func.to_char(validated_query.c.day, "YYYY-MM-DD"),
            func.count(validated_query.c.task_project),
        ).group_by(validated_query.c.day)
        tasks_validated = dict(tasks_validated_q.all())

        bad_imagery_query = (
            db.session.query(
                query.c.day.label("day"),
                tuple_(query.c.task_id, query.c.project_id).label("task_project"),
            )
            .select_from(query)
            .distinct(tuple_(query.c.task_id, query.c.project_id))
            .filter(query.c.action_text == "BADIMAGERY")
            .group_by(query.c.task_id, query.c.project_id, query.c.day)
            .order_by(query.c.task_id, query.c.project_id, query.c.day)
            .subquery()
        )
        tasks_bad_imagery_q = db.session.query(
            func.to_char(bad_imagery_query.c.day, "YYYY-MM-DD"),
            func.count(bad_imagery_query.c.task_project),
        ).group_by(bad_imagery_query.c.day)
        tasks_bad_imagery = dict(tasks_bad_imagery_q.all())

        dates = db.session.query(distinct(query.c.day)).select_from(query).all()
        dates = [r[0] for r in dates]
        day_stats_dto = list(map(StatsService.set_task_stats, dates))

        for dto in day_stats_dto:
            date = dto.date.strftime("%Y-%m-%d")
            try:
                dto.mapped = tasks_mapped[date] if date in tasks_mapped else 0
                dto.validated = tasks_validated[date] if date in tasks_validated else 0
                dto.bad_imagery = (
                    tasks_bad_imagery[date] if date in tasks_bad_imagery else 0
                )
            except Exception as e:
                print("Error", e)

        results_dto = TaskStatsDTO()
        results_dto.stats = day_stats_dto

        return results_dto
