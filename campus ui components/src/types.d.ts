export {};

declare global {
  interface Window {
    campusApi: {
      listApps: () => Promise<CampusApp[]>;
      getTheme: () => Promise<'light' | 'dark'>;
      setTheme: (mode: 'light' | 'dark') => Promise<'light' | 'dark'>;
      launchApp: (appId: string) => Promise<{ ok: boolean; error?: string; external?: boolean }>;
      windowAction: (action: 'minimize' | 'toggle-maximize' | 'close') => Promise<void>;
      onToolFailed?: (
        callback: (detail: { id: string; name: string; error: string }) => void,
      ) => () => void;
    };
  }

  interface CampusApp {
    id: string;
    name: string;
    category: string;
    detail: string;
    available: boolean;
    external?: boolean;
  }
}
