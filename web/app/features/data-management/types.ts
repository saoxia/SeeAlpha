export type DataTab = "operations" | "export";

export type DataFilters = {
  accountId: string;
  eventType: string;
  symbol: string;
  start: string;
  end: string;
};

export const emptyDataFilters: DataFilters = {
  accountId: "",
  eventType: "",
  symbol: "",
  start: "",
  end: "",
};
