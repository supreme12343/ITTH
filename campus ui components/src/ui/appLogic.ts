export interface CampusAppSummary {
  name: string;
  detail: string;
  category: string;
}

export function filterCampusApps<T extends CampusAppSummary>(apps: T[], filter: string, search: string): T[] {
  const term = search.trim().toLowerCase();
  return apps.filter((app) => (filter === 'All tools' || app.category === filter)
    && (!term || `${app.name} ${app.detail} ${app.category}`.toLowerCase().includes(term)));
}
